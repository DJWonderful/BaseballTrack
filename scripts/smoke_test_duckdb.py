"""Smoke-test every SQL query under the DuckDB backend.

Extracts query_df() call arguments from all page modules and runs each against
DuckDB. Reports which queries fail so we can fix dialect issues one-by-one.

Not perfect — queries with f-string interpolation or dynamic WHERE clauses need
representative parameter values. We cover most by substituting likely defaults.
"""

from __future__ import annotations

import os
import re
import sys
import traceback
from pathlib import Path

os.environ["APP_BACKEND"] = "duckdb"

# Stub streamlit caching decorators so we can import utils.db outside a session.
import types
st_stub = types.ModuleType("streamlit")
def _noop_dec(*a, **kw):
    def wrap(fn): return fn
    if a and callable(a[0]):
        return a[0]
    return wrap
st_stub.cache_data = _noop_dec
st_stub.cache_resource = _noop_dec
sys.modules["streamlit"] = st_stub

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "streamlit_app"))

from utils.db import _duck_conn  # noqa: E402

con = _duck_conn()

# Match SQL inside query_df("""...""") triple-quoted blocks
PATTERN = re.compile(r'query_df\(\s*(?:text\()?\s*f?"""(.*?)"""', re.DOTALL)

pages_dir = ROOT / "streamlit_app" / "pages"
home = ROOT / "streamlit_app" / "Home.py"
utils_files = list((ROOT / "streamlit_app" / "utils").glob("*.py"))

files = [home] + sorted(pages_dir.glob("*.py")) + utils_files

total = 0
failures: list[tuple[str, str, str]] = []

for fp in files:
    src = fp.read_text(encoding="utf-8")
    for m in PATTERN.finditer(src):
        sql = m.group(1)
        total += 1

        # Substitute representative values for common :param placeholders and
        # Python f-string interpolations.
        test_sql = sql
        # f-string braces → sane defaults
        test_sql = test_sql.replace("{team_id}", "401")
        test_sql = test_sql.replace("{season}", "2025")
        test_sql = test_sql.replace("{sport_id}", "12")
        test_sql = test_sql.replace("{level}", "'doublea'")
        test_sql = test_sql.replace("{game_type_clause}", "game_type IN ('R')")
        test_sql = test_sql.replace("{game_types_sql}", "game_type IN ('R')")
        test_sql = test_sql.replace("{game_types_where}", "game_type IN ('R')")
        # Bare placeholder patterns like {...}
        test_sql = re.sub(r"\{[^}]+\}", "1", test_sql)
        # Named params :xxx → DuckDB-safe literals. Use negative lookbehind so
        # we don't mangle PostgreSQL/DuckDB's ::type cast syntax (col::int).
        test_sql = re.sub(r"(?<!:):team_id\b", "401", test_sql)
        test_sql = re.sub(r"(?<!:):season\b", "2025", test_sql)
        test_sql = re.sub(r"(?<!:):sport_id\b", "12", test_sql)
        test_sql = re.sub(r"(?<!:):cluster_id\b", "1", test_sql)
        test_sql = re.sub(r"(?<!:):(\w+)", "NULL", test_sql)

        try:
            con.execute(test_sql).fetch_df()
        except Exception as e:
            failures.append((fp.name, sql[:120].strip(), str(e).split("\n")[0][:200]))

print(f"Ran {total} queries; {len(failures)} failed.\n")
for page, snippet, err in failures:
    print(f"[{page}] {snippet!r}")
    print(f"    ERR: {err}\n")
