# Deployment & Data Refresh

The app has two modes:

| Mode | Backend | Used for |
|------|---------|----------|
| **Local dev** | PostgreSQL (`milb` schema) | running collectors, writing recs, editing |
| **Deployed** | DuckDB reading Parquet in `data/app/` | Streamlit Cloud (read-only) |

The backend is selected by the `APP_BACKEND` env var. Default is `postgres`. On Streamlit Cloud, set `APP_BACKEND=duckdb` in the app's Secrets / Advanced Settings.

---

## First-time Streamlit Cloud setup

1. Create the app at [share.streamlit.io](https://share.streamlit.io), pointing to:
   - Repository: `DJWonderful/BaseballTrack`
   - Branch: `main`
   - Main file: `streamlit_app/app.py`
2. In **Advanced settings → Secrets** (or the app's `.streamlit/secrets.toml`), add:
   ```toml
   APP_BACKEND = "duckdb"
   ```
3. Click Deploy. First build takes ~2-3 min while `pip install` runs.

No DB credentials go to the cloud. The entire dataset ships as Parquet files in the repo.

---

## Refreshing the data

Whenever you want the deployed app to reflect new games, promotions, or analytics runs:

```bash
# 1. Run your normal local pipeline against Postgres
python scripts/collect_all.py               # or refresh.bat
python scripts/build_features.py --force
python scripts/analyze_promo_lift_counterfactual.py --force
python scripts/generate_recommendations.py --force
# ...any other analytics you want reflected

# 2. Dump Postgres → Parquet snapshot (~10 seconds)
python scripts/export_for_app.py

# 3. Commit and push
git add data/app/
git commit -m "refresh data YYYY-MM-DD"
git push

# 4. Streamlit Cloud auto-redeploys in ~30 seconds. Done.
```

Expected size per refresh: **~25-30 MB**. GitHub's per-file limit is 100 MB; we're well below.

---

## What the deployed app can't do

The deployed build reads Parquet, so it has no writable storage that survives a redeploy. Two surfaces are affected:

- **Recommendation tracking** ([10_Recommendations.py](../streamlit_app/pages/10_Recommendations.py)) — the Save button is hidden; existing statuses still display, but new changes must be made locally. Re-export to publish them.
- **Any future `execute()` call** — silently becomes a no-op in deployed mode. Pages should check `is_read_only()` before offering write UIs.

Everything else (filters, charts, exports, narratives) works identically in both modes.

---

## Under the hood

- [`scripts/export_for_app.py`](../scripts/export_for_app.py) dumps every base table + view in `milb.*` to `data/app/<name>.parquet`. JSONB columns are cast to TEXT; the app code already handles `json.loads(str)` on read.
- [`streamlit_app/utils/db.py`](../streamlit_app/utils/db.py) picks a backend at startup based on `APP_BACKEND`. DuckDB mode creates an in-memory DB and registers each Parquet file as a view under the `milb` schema, so every existing `SELECT ... FROM milb.games` keeps working.
- DuckDB is ~95% Postgres-compatible. One alias rename has been applied (`at` → `awt` in [4_Opponents.py](../streamlit_app/pages/4_Opponents.py)) because `AT` is a reserved keyword in DuckDB.
- The smoke test [`scripts/smoke_test_duckdb.py`](../scripts/smoke_test_duckdb.py) runs every `query_df(...)` call against DuckDB. Run it before pushing if you've added new queries.

---

## Troubleshooting

**"FileNotFoundError: data/app is missing" on deploy.**
You forgot to commit `data/app/` after running the exporter. `git status` should show the Parquet files as staged.

**Streamlit Cloud build fails on `duckdb` install.**
Check [requirements.txt](../requirements.txt) still pins `duckdb>=1.5` and `pyarrow>=15.0`. Both have pre-built Linux wheels — no native compile needed.

**A page errors with "Parser Error" after you add a new query.**
DuckDB doesn't support the exact Postgres syntax you used. Run [`scripts/smoke_test_duckdb.py`](../scripts/smoke_test_duckdb.py) to see which query; usually a minor rewrite fixes it (rename reserved-word aliases, swap `DISTINCT ON` for window-function `ROW_NUMBER()`, etc.).

**Recommendation Save button disappeared locally.**
Check your env — `APP_BACKEND=duckdb` shouldn't be set locally. Unset it or set it explicitly to `postgres`.
