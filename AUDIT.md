# Senior Engineering Audit — Invoice AI Pipeline

## Critical Bugs (would break at runtime)

### 1. XCom size bomb — DAG will crash on any real invoice batch
**File:** `airflow/dags/invoice_pipeline_dag.py`
**Problem:** The full OCR text and extracted JSON for *every* invoice is pushed through
Airflow XCom, which is stored in the metadata database. A single 10-page invoice OCR
result can be 50–200 KB. With 20 invoices in a batch, a single DAG run writes 4 MB to
the Airflow DB — far exceeding Airflow's default 48 KB XCom limit and causing silent
truncation or `OperationalError` crashes.
**Fix:** Each task writes its output to disk (already done for OCR/extraction JSON) and
pushes only file paths via XCom. Downstream tasks read from disk.

### 2. New engine + session created on every DB call — connection pool exhausted
**File:** `database/writer.py`, `database/models.py`
**Problem:** `get_engine()` is called inside `save_invoice_to_db()` and
`invoice_already_processed()` with no caching. Each call creates a new engine with its
own connection pool. Under Airflow's LocalExecutor with concurrent task runs, this
leaks connections until PostgreSQL's `max_connections` (default 100) is exhausted.
**Fix:** Use a module-level singleton engine via `functools.lru_cache` or a dedicated
`db.py` session context manager.

### 3. `--reload` flag in production Dockerfile
**File:** `docker/Dockerfile.upload`
**Problem:** `uvicorn ... --reload` watches the filesystem and restarts the process on
any file change. Inside Docker with volume mounts, this causes constant restarts. In
production it is a security and stability hazard.
**Fix:** Remove `--reload`. Use a separate `docker-compose.override.yml` for dev.

### 4. `invoice_already_processed` uses a LIKE query on the full path — false positives
**File:** `database/writer.py`
**Problem:** `Invoice.file_path.contains(file_path)` compiles to
`WHERE file_path LIKE '%<path>%'`. If two invoices share a common path substring
(e.g. both contain "invoice"), one will be incorrectly marked as already processed.
**Fix:** Use a dedicated `file_hash` column (SHA-256 of file contents) and do an exact
match, or use exact equality on the stored file name.

### 5. Race condition: duplicate processing under concurrent DAG runs
**File:** `airflow/dags/invoice_pipeline_dag.py`
**Problem:** `max_active_runs=1` prevents two DAG *runs* from overlapping, but the
detect→save sequence has no atomic file-claim step. If the schedule fires while the
previous run's `save_to_postgres` is in flight, the same file can be picked up twice
before the DB dedup check fires.
**Fix:** Move files to an `in_progress/` staging directory atomically in
`detect_new_invoices` before pushing paths to XCom.

---

## Architectural Problems

### 6. God `requirements.txt` — every image installs everything
**Problem:** The single `requirements.txt` contains Streamlit, FastAPI, Tesseract
bindings, OpenAI, and Airflow-adjacent packages. The upload service image installs
Streamlit. The dashboard image installs pytesseract. This bloats images, increases
build time, and violates separation of concerns.
**Fix:** Per-service requirements files:
- `requirements/base.txt` — shared (sqlalchemy, psycopg2, python-dotenv, pydantic)
- `requirements/upload.txt` — fastapi, uvicorn, python-multipart
- `requirements/ocr.txt` — pytesseract, pdf2image, Pillow
- `requirements/extraction.txt` — openai, requests (Airflow image only)
- `requirements/dashboard.txt` — streamlit, pandas, plotly

### 7. Config construction at module import time — env vars must be set before import
**File:** `config/settings.py`, `database/models.py`
**Problem:** `DATABASE_URL` and all config dataclasses are constructed when the module
is first imported. `os.getenv()` is called at definition time. If a `.env` file is not
sourced before Python starts, the defaults are silently baked in for the lifetime of the
process. There is also no validation that required variables (like `OPENAI_API_KEY`) are
actually set.
**Fix:** Use `pydantic-settings` `BaseSettings` which validates, casts, and lazily reads
from `.env` correctly, raising a clear error on startup for missing required fields.

### 8. No database migration system — `init.sql` and `create_tables()` are both present and conflict
**Problem:** `database/init.sql` is run by the Postgres Docker entrypoint on first boot.
`database/models.py::create_tables()` is called at runtime by the Airflow writer. These
two mechanisms can diverge: `init.sql` uses `DOUBLE PRECISION` while the ORM uses
`Float`. Adding a column in the future requires editing both files and hoping they stay
in sync.
**Fix:** Drop `create_tables()` from the writer. Use Alembic for all schema management.
`init.sql` only creates the database and role. Alembic `upgrade head` runs at container
start via an `entrypoint.sh`.

### 9. No error state tracking — failed invoices disappear silently
**Problem:** When OCR or LLM extraction fails, the error is logged but the PDF remains
in `raw/`. On the next DAG run, it will be retried forever with no record of previous
failures. There is no way to query "which invoices failed and why."
**Fix:** Add a `processing_status` table (or status column on invoices) with states:
`pending`, `ocr_failed`, `extraction_failed`, `complete`. Failed files are moved to
`data/invoices/failed/` with a corresponding error log.

### 10. Hardcoded Fernet key default in docker-compose
**File:** `docker-compose.yml` line 16
**Problem:** `AIRFLOW__CORE__FERNET_KEY:-zH5oKDjq3oUz_...` — a hardcoded default Fernet
key is shipped in the repository. Anyone who clones this and forgets to change `.env`
will run with a publicly known encryption key for all Airflow secrets.
**Fix:** Remove the default. Fail loudly at startup if not set. Add a `scripts/generate_secrets.sh` that generates a fresh key.

---

## Missing Files

| Missing File | Why It's Needed |
|---|---|
| `requirements/base.txt` + per-service files | Image separation |
| `alembic.ini` + `database/migrations/` | Schema versioning |
| `database/session.py` | Singleton session management |
| `services/upload_service/schemas.py` | Pydantic request/response models |
| `docker/entrypoint.airflow.sh` | Run `alembic upgrade head` before scheduler starts |
| `docker-compose.override.yml` | Dev-only settings (--reload, debug, port forwards) |
| `tests/unit/test_ocr_processor.py` | Unit tests |
| `tests/unit/test_extractor.py` | Unit tests |
| `tests/integration/test_upload_api.py` | Integration tests |
| `scripts/generate_secrets.sh` | One-time secret generation |
| `scripts/seed_demo_data.py` | Load demo invoices for dashboard smoke test |
| `.env.example` | Safe template to commit (no real secrets) |
| `Makefile` | Standard developer commands |
| `pyproject.toml` | Linting, formatting, test config |
| `dashboard/__init__.py` | Python package marker |
| `airflow/dags/__init__.py` | Python package marker |

---

## Scaling Problems

### 11. Single-task OCR processes all invoices serially in one Airflow task
Processing 50 invoices in sequence inside one `PythonOperator` means a single slow PDF
blocks the entire batch. If it crashes mid-batch, half the invoices are OCR'd and half
are not, leaving XCom state partially populated.
**Fix:** Use `TaskGroup` + dynamic task mapping (`expand()`) so each invoice is an
independent task that can be retried individually.

### 12. No connection pooling configuration on the database engine
**Problem:** SQLAlchemy default pool size is 5 with overflow 10. Under the Airflow
LocalExecutor running 4+ concurrent tasks, each creating its own engine, this silently
queues connections and degrades throughput.
**Fix:** Explicit pool settings in the singleton engine; NullPool for Airflow workers
(they are short-lived processes that should not hold connections between tasks).

### 13. `@st.cache_data(ttl=30)` loads *all* invoices with no pagination
**Problem:** The Streamlit dashboard runs `SELECT * FROM invoices` with no LIMIT.
With 100,000 invoices this will load hundreds of MB into the Streamlit process and
time out.
**Fix:** Add pagination with LIMIT/OFFSET, or use date-range filters with indexed
columns before fetching.
