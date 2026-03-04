# 🧾 AI Invoice Processing Pipeline

A production-minded, fully local invoice processing system. Upload a PDF, and the pipeline automatically extracts structured data using OCR and an LLM, stores it in PostgreSQL, and visualises analytics in a real-time Streamlit dashboard-all orchestrated by Apache Airflow and containerised with Docker Compose.

---

## 📐 Architecture

```
User Upload (PDF)
      │
      ▼
┌─────────────────────────┐
│  FastAPI Upload Service  │  POST /api/v1/upload-invoice
│  localhost:8000          │  • Validates file type & size
└────────┬────────────────┘  • Computes SHA-256 hash (dedup)
         │ atomic rename      • Registers pending DB record
         ▼
┌─────────────────────────┐
│  data/invoices/raw/      │  PDFs waiting to be processed
└────────┬────────────────┘
         │ claimed atomically → in_progress/
         ▼
┌─────────────────────────┐
│  Apache Airflow DAG      │  Runs every 5 minutes
│  localhost:8080          │  max_active_runs = 1
└────────┬────────────────┘
         │
    ┌────┴──────────────────────┐
    ▼                           ▼ (on failure)
┌──────────────┐        ┌──────────────────┐
│  Tesseract   │        │  data/invoices/  │
│  OCR         │        │  failed/         │
│  pdf2image   │        │  + DB status     │
└──────┬───────┘        └──────────────────┘
       │ JSON → data/ocr/
       ▼
┌──────────────────────┐
│  LLM Extraction      │  OpenAI API or local Ollama
│  Structured JSON     │  JSON → data/extracted/
└──────────┬───────────┘
           │ file path only (not full blob) via XCom
           ▼
┌───────────────────────┐
│  PostgreSQL           │  invoices + line_items tables
│  Alembic migrations   │  SHA-256 dedup, status tracking
└───────────┬───────────┘
            │
            ▼
┌───────────────────────┐
│  Streamlit Dashboard  │  Paginated queries, failure view
│  localhost:8501       │  KPIs, charts, invoice table
└───────────────────────┘
```

---

## 🗂 Repository Structure

```
invoice_ai_pipeline_v2/
│
├── services/
│   ├── upload_service/
│   │   ├── main.py          # FastAPI app entry point
│   │   ├── routes.py        # API route handlers
│   │   ├── schemas.py       # Pydantic request/response models
│   │   └── storage.py       # File I/O, SHA-256 hashing, dir management
│   │
│   ├── ocr_service/
│   │   └── ocr_processor.py # PDF → image → Tesseract → JSON file
│   │
│   └── extraction_service/
│       ├── extractor.py         # LLM field extraction, JSON parsing
│       └── prompt_templates.py  # Centralised prompt definitions
│
├── airflow/
│   └── dags/
│       └── invoice_pipeline_dag.py  # 4-task DAG with XCom path-only pattern
│
├── database/
│   ├── init.sql             # PostgreSQL bootstrap (extensions only)
│   ├── models.py            # SQLAlchemy ORM models (Invoice, LineItem)
│   ├── session.py           # Singleton engine + session context manager
│   ├── writer.py            # SHA-256 dedup, status tracking, DB writes
│   └── migrations/
│       ├── env.py           # Alembic environment
│       └── versions/
│           └── 0001_initial_schema.py
│
├── dashboard/
│   └── app.py               # Streamlit dashboard (paginated, failure view)
│
├── config/
│   └── settings.py          # pydantic-settings validated config
│
├── tests/
│   ├── unit/
│   │   ├── test_ocr_processor.py
│   │   ├── test_extractor.py
│   │   └── test_writer.py
│   └── integration/
│       └── test_upload_api.py
│
├── docker/
│   ├── Dockerfile.upload         # FastAPI image (upload deps only)
│   ├── Dockerfile.airflow        # Airflow + Tesseract + worker deps
│   ├── Dockerfile.dashboard      # Streamlit image (dashboard deps only)
│   └── entrypoint.airflow.sh     # Runs alembic upgrade head before scheduler
│
├── scripts/
│   ├── generate_secrets.sh  # Generates Fernet key, web secret, passwords
│   └── seed_demo_data.py    # Inserts 50 synthetic invoices for testing
│
├── requirements/
│   ├── base.txt             # Shared: sqlalchemy, psycopg2, pydantic
│   ├── upload.txt           # FastAPI, uvicorn, multipart
│   ├── worker.txt           # pytesseract, pdf2image, openai
│   ├── dashboard.txt        # streamlit, pandas, plotly
│   └── test.txt             # pytest, httpx, factory-boy
│
├── data/
│   └── invoices/
│       ├── raw/             # Uploaded PDFs awaiting the DAG
│       ├── in_progress/     # Atomically claimed during a DAG run
│       ├── processed/       # Successfully completed
│       └── failed/          # OCR or extraction failures
│
├── alembic.ini
├── docker-compose.yml
├── docker-compose.override.yml  # Dev-only (hot reload, debug)
├── Makefile
├── pyproject.toml
├── .env.example
├── .gitignore
└── README.md
```

---

## 🚀 Quick Start

### Prerequisites

- Docker ≥ 24.x and Docker Compose ≥ 2.x
- An OpenAI API key **or** a running [Ollama](https://ollama.ai) instance for local LLM

### 1. Set up environment

```bash
git clone https://github.com/your-org/invoice_ai_pipeline_v2.git
cd invoice_ai_pipeline_v2

# Create your .env from the safe template
cp .env.example .env

# Auto-generate cryptographic keys and passwords
bash scripts/generate_secrets.sh
# → Copy the printed values into .env

# Add your OpenAI key (or configure Ollama-see below)
echo "OPENAI_API_KEY=sk-your-key-here" >> .env
```

### 2. Start all services

```bash
docker compose up --build
```

First boot takes 3–5 minutes. Services start in dependency order (Postgres → Airflow init → Scheduler + Webserver → Upload API + Dashboard).

### 3. Verify everything is running

```bash
make ps
```

| Service | URL | Default credentials |
|---|---|---|
| FastAPI Upload API | http://localhost:8000/docs |-|
| Airflow UI | http://localhost:8080 | admin / (your .env password) |
| Streamlit Dashboard | http://localhost:8501 |-|
| PostgreSQL | localhost:5432 | see .env |

---

## 📤 Uploading Invoices

### Via Swagger UI (easiest)

1. Open http://localhost:8000/docs
2. Expand `POST /api/v1/upload-invoice`
3. Click **Try it out** → **Choose File** → select a PDF → **Execute**

### Via curl

```bash
curl -X POST http://localhost:8000/api/v1/upload-invoice \
  -F "file=@/path/to/invoice.pdf"
```

### Via Makefile shortcut

```bash
# Upload the included sample invoice
make upload-sample
```

### Response

```json
{
  "status": "uploaded",
  "file_id": "a3f1c2b4-...",
  "file_hash": "e3b0c44298fc1c149...",
  "original_filename": "invoice_acme.pdf",
  "file_path": "/app/data/invoices/raw/20240315_143022_invoice_acme_a3f1c2.pdf",
  "message": "Invoice queued for processing. Check the Airflow dashboard for pipeline status."
}
```

The Airflow DAG picks up the file within 5 minutes automatically. To trigger immediately:

```bash
make trigger-dag
```

---

## ⚙️ Pipeline Details

The DAG `invoice_processing_pipeline` runs on a 5-minute schedule with `max_active_runs=1`.

### Task 1-`detect_new_invoices`

Scans `data/invoices/raw/` and **atomically renames** each PDF into `data/invoices/in_progress/` before pushing the paths via XCom. This prevents duplicate processing if a DAG run overlaps with the next scheduled run.

### Task 2-`run_ocr`

For each claimed PDF:
- Converts pages to images using `pdf2image` at configured DPI (default 300)
- Runs Tesseract OCR (`--psm 6 --oem 3`) on each page
- Saves a JSON result to `data/ocr/<stem>_ocr.json`
- Pushes only the **file path** via XCom (not the text blob)

On failure: updates the invoice `processing_status` to `ocr_failed` in the DB and moves the PDF to `data/invoices/failed/`.

```json
{
  "file_name": "invoice_acme.pdf",
  "page_count": 2,
  "text_blocks": ["Invoice #: 4567", "Vendor: ACME Corp", "Total: $1,200"],
  "full_text": "..."
}
```

### Task 3-`run_llm_extraction`

Reads OCR JSON from disk, sends the `full_text` to the configured LLM with a structured extraction prompt. Saves the result to `data/extracted/<stem>_extracted.json` and pushes the file path via XCom.

On failure: updates status to `extraction_failed`.

```json
{
  "invoice_number": "4567",
  "vendor": "ACME Corp",
  "invoice_date": "2024-01-10",
  "due_date": "2024-02-10",
  "total_amount": 1200.0,
  "tax_amount": 100.0,
  "currency": "USD",
  "confidence": 0.92,
  "line_items": [
    { "description": "Consulting", "quantity": 8, "unit_price": 137.5, "total": 1100.0 }
  ]
}
```

### Task 4-`save_to_postgres`

- Computes SHA-256 of the source PDF for exact deduplication (no false positives from path substring matching)
- Upserts the `Invoice` and `LineItem` rows
- Sets `processing_status = 'complete'`
- Moves the PDF from `in_progress/` to `processed/`

---

## 🗄 Database Schema

### `invoices`

| Column | Type | Description |
|---|---|---|
| `id` | SERIAL PK | Auto-increment primary key |
| `file_path` | TEXT | Absolute path to source PDF |
| `file_hash` | VARCHAR(64) UNIQUE | SHA-256 hex digest for deduplication |
| `processing_status` | VARCHAR(20) | `pending` \| `ocr_failed` \| `extraction_failed` \| `complete` |
| `processing_error` | TEXT | Error detail when status is a failure |
| `invoice_number` | VARCHAR(100) | Extracted invoice identifier |
| `vendor` | VARCHAR(255) | Issuing company/person |
| `invoice_date` | DATE | Issue date (proper Date column, not varchar) |
| `due_date` | DATE | Payment due date |
| `total_amount` | FLOAT | Total including tax |
| `tax_amount` | FLOAT | Tax / VAT amount |
| `currency` | VARCHAR(10) | ISO 4217 currency code |
| `confidence` | FLOAT | LLM extraction confidence (0–1) |
| `created_at` | TIMESTAMPTZ | Pipeline processing timestamp |
| `updated_at` | TIMESTAMPTZ | Last update timestamp |

### `line_items`

| Column | Type | Description |
|---|---|---|
| `id` | SERIAL PK | Auto-increment primary key |
| `invoice_id` | INTEGER FK | References `invoices.id` (CASCADE DELETE) |
| `description` | TEXT | Line item description |
| `quantity` | FLOAT | Item quantity |
| `unit_price` | FLOAT | Per-unit price |
| `total` | FLOAT | Line total |

Schema is managed exclusively by **Alembic** migrations. The migration runs automatically via `docker/entrypoint.airflow.sh` on every container start, so schema changes are always applied without manual steps.

---

## 🖥 Dashboard

The Streamlit dashboard at http://localhost:8501 provides:

- **KPI row**: total processed, pending, failed, total spend, average confidence
- **Vendor spending bar chart**: top 20 vendors by total invoice value
- **Confidence histogram**: distribution with mean marker
- **Daily volume timeline**: invoices processed per day
- **Daily spend area chart**: cumulative spend over time
- **Paginated invoice table**: filter by vendor, date range, confidence, status
- **Failed invoice detail view**: expandable panel showing error messages

Data refreshes every 30 seconds. Click **🔄 Refresh** for immediate reload.

---

## 🔧 Using a Local LLM (Ollama-no API key required)

1. Install [Ollama](https://ollama.ai) on your host machine
2. Pull a capable model: `ollama pull mistral` or `ollama pull llama3`
3. Update `.env`:

```bash
LLM_PROVIDER=local
LLM_MODEL=mistral
LOCAL_LLM_URL=http://host.docker.internal:11434
```

4. Restart: `docker compose up --build`

---

## 🛠 Developer Workflow

### Makefile commands

```bash
make up           # Start all services (detached)
make down         # Stop containers
make destroy      # Stop + wipe volumes (full reset)
make logs         # Tail all service logs
make ps           # Show service status

make up-dev       # Start with hot reload (override file)
make test         # Run full test suite
make test-unit    # Unit tests only
make lint         # Ruff linter
make format       # Auto-format

make shell-db     # Open psql
make trigger-dag  # Manually fire the Airflow DAG
make seed         # Insert 50 demo invoices
make secrets      # Generate new cryptographic keys
```

### Run tests locally

```bash
# Inside upload_service container
docker compose exec upload_service pytest tests/ -v

# Or locally with a venv
pip install -r requirements/test.txt
pytest tests/unit/ -v
```

### Connect to PostgreSQL

```bash
make shell-db
# or
docker compose exec postgres psql -U invoice_user -d invoice_db
```

### View Airflow logs

```bash
docker compose logs airflow_scheduler -f
```

### Add a database migration

```bash
# Generate a new migration from model changes
docker compose exec airflow_scheduler \
  alembic revision --autogenerate -m "add_payment_method_column"

# Apply it
make migrate
```

---

## 🔒 Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `POSTGRES_PASSWORD` | ✅ |-| PostgreSQL password |
| `POSTGRES_USER` | | `invoice_user` | PostgreSQL user |
| `POSTGRES_DB` | | `invoice_db` | Database name |
| `POSTGRES_HOST` | | `postgres` | DB host (Docker service name) |
| `OPENAI_API_KEY` | ✅ (if openai) |-| OpenAI API key |
| `LLM_PROVIDER` | | `openai` | `openai` or `local` |
| `LLM_MODEL` | | `gpt-3.5-turbo` | Model name |
| `LOCAL_LLM_URL` | | `http://host.docker.internal:11434` | Ollama endpoint |
| `AIRFLOW__CORE__FERNET_KEY` | ✅ |-| Airflow encryption key (generate with `make secrets`) |
| `AIRFLOW__WEBSERVER__SECRET_KEY` | ✅ |-| Airflow web session key |
| `AIRFLOW_ADMIN_PASSWORD` | ✅ |-| Airflow UI admin password |
| `MAX_UPLOAD_SIZE_MB` | | `50` | Maximum PDF upload size |
| `OCR_DPI` | | `300` | Tesseract rendering resolution |
| `LOG_LEVEL` | | `INFO` | `DEBUG` / `INFO` / `WARNING` |

Generate all required secrets in one step:

```bash
bash scripts/generate_secrets.sh
```

---

## 📦 Tech Stack

| Component | Technology | Version |
|---|---|---|
| Upload API | FastAPI + Uvicorn | 0.110 / 0.29 |
| Orchestration | Apache Airflow | 2.8.1 |
| OCR | Tesseract + pdf2image | 0.3.10 / 1.17 |
| LLM | OpenAI API / Ollama | openai 1.23 |
| Database | PostgreSQL | 15 |
| ORM + Migrations | SQLAlchemy + Alembic | 2.0 / 1.13 |
| Dashboard | Streamlit + Plotly | 1.33 / 5.21 |
| Config | pydantic-settings | 2.2 |
| Testing | pytest + httpx | 8.2 / 0.27 |
| Containers | Docker + Compose |-|
| Language | Python | 3.10 |

---

## 🐛 Troubleshooting

**Airflow webserver shows "Fernet key not set"**
→ Run `bash scripts/generate_secrets.sh`, add the key to `.env`, restart with `make down && make up`.

**Upload returns 409 Conflict**
→ The same PDF has already been processed. The SHA-256 hash matches an existing record. Upload a different file or check `data/invoices/processed/`.

**Invoice stuck in `pending` status**
→ The Airflow DAG hasn't run yet. Trigger manually: `make trigger-dag`. Check scheduler logs: `docker compose logs airflow_scheduler`.

**OCR produces empty or garbled text**
→ Try increasing `OCR_DPI=400` in `.env`. Scanned PDFs at low resolution produce poor Tesseract output. Ensure Tesseract English language data is installed (`tesseract-ocr-eng`).

**Dashboard shows "No invoices match filters"**
→ Run `make seed` to insert 50 synthetic demo invoices, then reload the dashboard.

