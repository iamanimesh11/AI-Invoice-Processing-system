"""
services/upload_service/routes.py
FastAPI route definitions for the invoice upload service.

FIX from v1:
- Response model now uses typed Pydantic schemas (schemas.py) — OpenAPI docs
  show correct response shapes instead of untyped dicts.
- File size validated against MAX_UPLOAD_SIZE_MB setting before saving.
- file_hash returned in response so clients can detect duplicate uploads.
- Registers a pending DB record at upload time so the pipeline status is
  immediately visible in the database.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse

from config.settings import get_settings
from services.upload_service.schemas import (
    ErrorResponse,
    InvoiceListResponse,
    UploadResponse,
)
from services.upload_service.storage import list_raw_invoices, save_invoice_file

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["Invoice Upload"])


@router.post(
    "/upload-invoice",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid file type or size"},
        500: {"model": ErrorResponse, "description": "Storage error"},
    },
    summary="Upload a PDF invoice for processing",
)
async def upload_invoice(file: UploadFile = File(...)) -> UploadResponse:
    """
    Accept a multipart PDF upload, persist it to local storage, and
    register a pending processing record in the database.
    """
    settings = get_settings()

    # ── Content-type validation ────────────────────────────────────────────────
    allowed_types = {"application/pdf", "application/octet-stream"}
    if file.content_type not in allowed_types or not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only PDF files are accepted. Received content-type: {file.content_type!r}",
        )

    # ── Read & size check ──────────────────────────────────────────────────────
    contents = await file.read()
    if len(contents) > settings.max_upload_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"File size {len(contents) / 1024 / 1024:.1f} MB exceeds "
                f"the {settings.max_upload_size_mb} MB limit."
            ),
        )
    if len(contents) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )

    # ── Save to disk ───────────────────────────────────────────────────────────
    try:
        saved_path, file_id, file_hash = save_invoice_file(contents, file.filename)
    except Exception as exc:
        logger.exception("Failed to save invoice file: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save the uploaded file. Please try again.",
        ) from exc

    # ── Register pending DB record ─────────────────────────────────────────────
    try:
        from database.writer import invoice_already_processed, register_invoice_pending
        if invoice_already_processed(file_hash):
            logger.info("Duplicate upload rejected: hash=%s…", file_hash[:12])
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This invoice has already been processed (duplicate file content).",
            )
        register_invoice_pending(saved_path, file_hash)
    except HTTPException:
        raise
    except Exception as exc:
        # Non-fatal: DB registration failure should not block the upload
        logger.warning("Could not register pending invoice in DB: %s", exc)

    logger.info(
        "Invoice uploaded: file_id=%s filename=%r path=%s",
        file_id, file.filename, saved_path,
    )
    return UploadResponse(
        status="uploaded",
        file_id=file_id,
        file_hash=file_hash,
        original_filename=file.filename,
        file_path=saved_path,
        message="Invoice queued for processing. Check the Airflow dashboard for pipeline status.",
    )


@router.get(
    "/invoices",
    response_model=InvoiceListResponse,
    summary="List raw invoices awaiting processing",
)
async def list_invoices() -> InvoiceListResponse:
    """Return metadata for all PDFs in the raw invoice directory."""
    invoices = list_raw_invoices()
    return InvoiceListResponse(count=len(invoices), invoices=invoices)
