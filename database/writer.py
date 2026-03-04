"""
database/writer.py
Helper functions for persisting extracted invoice data to PostgreSQL.

FIX from v1:
- Deduplication now uses SHA-256 file_hash (exact match) instead of
  LIKE on file_path, which caused false positive duplicate detection.
- Processing status (pending / ocr_failed / extraction_failed / complete)
  is written to the DB so failed invoices are visible and queryable.
- Session management delegated to database.session.get_db_session().
  No more per-call engine/session creation that exhausted connection pools.
- Date strings parsed to Python date objects before insertion.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional

from database.models import Invoice, LineItem
from database.session import get_db_session

logger = logging.getLogger(__name__)


# ── Hashing ────────────────────────────────────────────────────────────────────

def compute_file_hash(file_path: str) -> str:
    """
    Compute the SHA-256 hex digest of a file.

    Args:
        file_path: Absolute path to the file.

    Returns:
        64-character lowercase hex string.
    """
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def compute_bytes_hash(data: bytes) -> str:
    """Compute SHA-256 of raw bytes (used at upload time before saving)."""
    return hashlib.sha256(data).hexdigest()


# ── Deduplication ──────────────────────────────────────────────────────────────

def find_invoice_by_hash(file_hash: str) -> Optional[Invoice]:
    """
    Look up an invoice by its file SHA-256 hash.

    Returns the Invoice ORM object if found, else None.
    """
    with get_db_session() as session:
        return session.query(Invoice).filter(Invoice.file_hash == file_hash).first()


def invoice_already_processed(file_hash: str) -> bool:
    """
    Return True if an invoice with this file_hash already has status='complete'.

    Using file_hash (exact SHA-256 match) instead of a LIKE on file_path
    eliminates false positives from shared path substrings.
    """
    with get_db_session() as session:
        count = (
            session.query(Invoice)
            .filter(Invoice.file_hash == file_hash, Invoice.processing_status == "complete")
            .count()
        )
        return count > 0


# ── Status updates ─────────────────────────────────────────────────────────────

def register_invoice_pending(file_path: str, file_hash: str) -> int:
    """
    Insert a pending invoice record at upload time.
    Returns the new invoice id.
    """
    with get_db_session() as session:
        inv = Invoice(
            file_path=file_path,
            file_hash=file_hash,
            processing_status="pending",
        )
        session.add(inv)
        session.commit()
        logger.info("Registered pending invoice: %s (hash=%s)", file_path, file_hash[:12])
        return inv.id


def mark_invoice_failed(file_hash: str, stage: str, error: str) -> None:
    """
    Update an invoice's processing status to a failure state.

    Args:
        file_hash: SHA-256 of the invoice file.
        stage: One of 'ocr_failed' or 'extraction_failed'.
        error: Human-readable error description.
    """
    with get_db_session() as session:
        inv = session.query(Invoice).filter(Invoice.file_hash == file_hash).first()
        if inv:
            inv.processing_status = stage
            inv.processing_error = error[:2000]  # Truncate to column limit
            session.commit()
            logger.warning("Invoice marked %s: hash=%s", stage, file_hash[:12])


# ── Full persistence ───────────────────────────────────────────────────────────

def _parse_date(value: Any) -> Optional[date]:
    """Safely parse a date string (YYYY-MM-DD) to a Python date object."""
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except (ValueError, TypeError):
            pass
    return None


def save_invoice_to_db(extracted: Dict[str, Any], file_hash: str) -> int:
    """
    Persist a fully extracted invoice and its line items to PostgreSQL.

    Args:
        extracted: Dict from the extraction service with all invoice fields.
        file_hash: SHA-256 hex digest of the source PDF file.

    Returns:
        The invoice's primary key id.

    Raises:
        Exception: Re-raises DB errors after rollback.
    """
    with get_db_session() as session:
        # Upsert pattern: update existing pending record if present
        inv = session.query(Invoice).filter(Invoice.file_hash == file_hash).first()

        if inv is None:
            inv = Invoice(file_hash=file_hash)
            session.add(inv)

        inv.file_path = extracted.get("file_path", "")
        inv.invoice_number = extracted.get("invoice_number")
        inv.vendor = extracted.get("vendor")
        inv.invoice_date = _parse_date(extracted.get("invoice_date"))
        inv.due_date = _parse_date(extracted.get("due_date"))
        inv.total_amount = float(extracted.get("total_amount") or 0)
        inv.tax_amount = float(extracted.get("tax_amount") or 0)
        inv.currency = extracted.get("currency") or "USD"
        inv.confidence = float(extracted.get("confidence") or 0)
        inv.processing_status = "complete"
        inv.processing_error = None

        session.flush()  # Ensure inv.id is populated before inserting children

        # Replace any previously inserted (partial) line items
        session.query(LineItem).filter(LineItem.invoice_id == inv.id).delete()

        for item in extracted.get("line_items", []) or []:
            session.add(LineItem(
                invoice_id=inv.id,
                description=item.get("description"),
                quantity=float(item.get("quantity") or 1),
                unit_price=float(item.get("unit_price") or 0),
                total=float(item.get("total") or 0),
            ))

        session.commit()
        logger.info(
            "Invoice saved: id=%d vendor=%r number=%r confidence=%.2f",
            inv.id, inv.vendor, inv.invoice_number, inv.confidence,
        )
        return inv.id
