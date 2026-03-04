"""
airflow/dags/invoice_pipeline_dag.py
Invoice processing pipeline — detect → OCR → extract → persist.

FIX from v1 (critical):

1. XCom size bomb eliminated: Tasks now push only file path strings via XCom
   (not full OCR text or extracted dicts). Downstream tasks read from disk.
   This keeps XCom well under Airflow's 48 KB default metadata DB limit.

2. Race condition eliminated: detect_new_invoices() atomically moves PDFs
   into an in_progress/ staging directory before pushing paths. Subsequent
   DAG runs cannot pick up the same file.

3. Failure tracking: OCR and extraction failures update the invoice's
   processing_status in the DB ('ocr_failed' / 'extraction_failed') and
   move the PDF to data/invoices/failed/ for human review.

4. Hardcoded Fernet key default removed from docker-compose; validated at
   settings load time.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator

sys.path.insert(0, "/app")

logger = logging.getLogger(__name__)

DEFAULT_ARGS = {
    "owner": "invoice_pipeline",
    "depends_on_past": False,
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
    "start_date": datetime(2024, 1, 1),
}


# ── Task 1 ─────────────────────────────────────────────────────────────────────

def detect_new_invoices(**context) -> None:
    """
    Scan raw/ for PDF files and atomically move them to in_progress/.

    FIX: Atomic move (rename) eliminates the race condition where two
    concurrent DAG runs could detect the same files. Files in in_progress/
    are invisible to the next DAG run's detect step.

    Pushes: List[str] of in_progress file paths → XCom key 'invoice_paths'.
    """
    from config.settings import get_settings
    from services.upload_service.storage import claim_invoice_for_processing

    settings = get_settings()
    raw_dir = Path(settings.invoices_raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)

    pdf_files = sorted(raw_dir.glob("*.pdf"))
    claimed_paths = []

    for pdf in pdf_files:
        try:
            in_progress_path = claim_invoice_for_processing(str(pdf))
            claimed_paths.append(in_progress_path)
        except Exception as exc:
            logger.warning("Could not claim %s: %s", pdf.name, exc)

    logger.info("Claimed %d invoice(s) for this run.", len(claimed_paths))
    context["ti"].xcom_push(key="invoice_paths", value=claimed_paths)


# ── Task 2 ─────────────────────────────────────────────────────────────────────

def run_ocr(**context) -> None:
    """
    OCR each claimed invoice PDF. Pushes {pdf_path → ocr_json_path} via XCom.

    FIX: Pushes only the path to the saved OCR JSON file, not the text content.
    Failures are recorded in the DB and the PDF is moved to failed/.
    """
    from services.ocr_service.ocr_processor import process_invoice_pdf
    from database.writer import mark_invoice_failed, compute_file_hash
    from services.upload_service.storage import mark_as_failed

    invoice_paths = context["ti"].xcom_pull(key="invoice_paths", task_ids="detect_new_invoices")
    if not invoice_paths:
        logger.info("No invoices to OCR this run.")
        context["ti"].xcom_push(key="ocr_path_map", value={})
        return

    ocr_path_map: dict[str, str] = {}  # pdf_path → ocr_json_path

    for pdf_path in invoice_paths:
        try:
            ocr_json_path = process_invoice_pdf(pdf_path)
            ocr_path_map[pdf_path] = ocr_json_path
            logger.info("OCR done: %s", Path(pdf_path).name)
        except Exception as exc:
            logger.error("OCR failed for %s: %s", pdf_path, exc)
            try:
                fhash = compute_file_hash(pdf_path)
                mark_invoice_failed(fhash, "ocr_failed", str(exc))
                mark_as_failed(pdf_path)
            except Exception as inner:
                logger.error("Could not record OCR failure: %s", inner)

    context["ti"].xcom_push(key="ocr_path_map", value=ocr_path_map)


# ── Task 3 ─────────────────────────────────────────────────────────────────────

def run_llm_extraction(**context) -> None:
    """
    Read each OCR JSON from disk, call LLM, write extraction JSON to disk.
    Pushes {pdf_path → extraction_json_path} via XCom.
    """
    from services.ocr_service.ocr_processor import load_ocr_result
    from services.extraction_service.extractor import extract_invoice_fields, ExtractionError
    from database.writer import mark_invoice_failed, compute_file_hash
    from services.upload_service.storage import mark_as_failed

    ocr_path_map = context["ti"].xcom_pull(key="ocr_path_map", task_ids="run_ocr")
    if not ocr_path_map:
        logger.info("No OCR results to extract.")
        context["ti"].xcom_push(key="extraction_path_map", value={})
        return

    extraction_path_map: dict[str, str] = {}  # pdf_path → extraction_json_path

    for pdf_path, ocr_json_path in ocr_path_map.items():
        pdf_name = Path(pdf_path).name
        try:
            ocr_data = load_ocr_result(ocr_json_path)
            extraction_json_path = extract_invoice_fields(
                ocr_text=ocr_data["full_text"],
                pdf_name=pdf_name,
                file_path=pdf_path,
            )
            extraction_path_map[pdf_path] = extraction_json_path
            logger.info("Extraction done: %s", pdf_name)
        except ExtractionError as exc:
            logger.error("Extraction failed for %s: %s", pdf_name, exc)
            try:
                fhash = compute_file_hash(pdf_path)
                mark_invoice_failed(fhash, "extraction_failed", str(exc))
                mark_as_failed(pdf_path)
            except Exception as inner:
                logger.error("Could not record extraction failure: %s", inner)

    context["ti"].xcom_push(key="extraction_path_map", value=extraction_path_map)


# ── Task 4 ─────────────────────────────────────────────────────────────────────

def save_to_postgres(**context) -> None:
    """
    Read each extraction JSON from disk, persist to PostgreSQL, move PDF to processed/.

    FIX: Deduplication uses SHA-256 file_hash (exact match) instead of
    LIKE on file_path.
    """
    from services.extraction_service.extractor import load_extracted_result
    from database.writer import (
        save_invoice_to_db,
        invoice_already_processed,
        compute_file_hash,
    )
    from services.upload_service.storage import mark_as_processed

    extraction_path_map = context["ti"].xcom_pull(
        key="extraction_path_map", task_ids="run_llm_extraction"
    )
    if not extraction_path_map:
        logger.info("No extraction results to save.")
        return

    saved = 0
    for pdf_path, extraction_json_path in extraction_path_map.items():
        pdf_name = Path(pdf_path).name
        try:
            file_hash = compute_file_hash(pdf_path)

            if invoice_already_processed(file_hash):
                logger.info("Skipping duplicate: %s", pdf_name)
                mark_as_processed(pdf_path)
                continue

            extracted = load_extracted_result(extraction_json_path)
            invoice_id = save_invoice_to_db(extracted, file_hash)
            mark_as_processed(pdf_path)
            logger.info("Saved invoice id=%d: %s", invoice_id, pdf_name)
            saved += 1
        except Exception as exc:
            logger.error("DB save failed for %s: %s", pdf_name, exc)

    logger.info("Pipeline run complete — %d invoice(s) saved to DB.", saved)


# ── DAG ────────────────────────────────────────────────────────────────────────

with DAG(
    dag_id="invoice_processing_pipeline",
    default_args=DEFAULT_ARGS,
    description="PDF invoice → OCR → LLM extraction → PostgreSQL",
    schedule_interval="*/5 * * * *",
    catchup=False,
    tags=["invoice", "ocr", "llm", "etl"],
    max_active_runs=1,
    doc_md=__doc__,
) as dag:

    t1 = PythonOperator(task_id="detect_new_invoices", python_callable=detect_new_invoices)
    t2 = PythonOperator(task_id="run_ocr",             python_callable=run_ocr)
    t3 = PythonOperator(task_id="run_llm_extraction",  python_callable=run_llm_extraction)
    t4 = PythonOperator(task_id="save_to_postgres",    python_callable=save_to_postgres)

    t1 >> t2 >> t3 >> t4
