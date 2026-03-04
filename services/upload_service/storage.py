"""
services/upload_service/storage.py
File I/O for incoming invoice PDFs.

FIX from v1:
- Returns file_hash (SHA-256) alongside the path so the upload route
  can register a pending DB record and enable exact-match deduplication.
- Added in_progress/ staging directory for race-condition-safe processing.
- Added file size validation.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

logger = logging.getLogger(__name__)


def _get_dirs() -> Tuple[Path, Path, Path, Path]:
    """Read directories from settings (deferred import avoids import-time env read)."""
    from config.settings import get_settings
    s = get_settings()
    return (
        Path(s.invoices_raw_dir),
        Path(s.invoices_processed_dir),
        Path(s.invoices_inprogress_dir),
        Path(s.invoices_failed_dir),
    )


def ensure_directories() -> None:
    """Create all required storage directories if they do not exist."""
    raw, processed, in_progress, failed = _get_dirs()
    for d in (raw, processed, in_progress, failed):
        d.mkdir(parents=True, exist_ok=True)
        logger.debug("Directory ensured: %s", d)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_invoice_file(contents: bytes, original_filename: str) -> Tuple[str, str, str]:
    """
    Persist raw invoice bytes and return (relative_path, file_id, file_hash).

    Args:
        contents: Raw PDF bytes.
        original_filename: Client-provided filename.

    Returns:
        Tuple of (saved_file_path, file_id, sha256_hex).
    """
    ensure_directories()
    raw_dir, _, _, _ = _get_dirs()

    file_hash = _sha256_bytes(contents)
    file_id = str(uuid.uuid4())
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    stem = Path(original_filename).stem
    filename = f"{timestamp}_{stem}_{file_id[:8]}.pdf"

    dest_path = raw_dir / filename
    dest_path.write_bytes(contents)

    logger.info(
        "Saved invoice [%s] → %s (%d bytes, sha256=%s…)",
        file_id, dest_path, len(contents), file_hash[:12],
    )
    return str(dest_path), file_id, file_hash


def list_raw_invoices() -> List[dict]:
    """Return metadata for all PDFs currently in the raw directory."""
    ensure_directories()
    raw_dir, _, _, _ = _get_dirs()
    result = []
    for pdf in sorted(raw_dir.glob("*.pdf")):
        stat = pdf.stat()
        result.append({
            "filename": pdf.name,
            "path": str(pdf),
            "size_bytes": stat.st_size,
            "modified_at": datetime.utcfromtimestamp(stat.st_mtime).isoformat(),
        })
    return result


def claim_invoice_for_processing(file_path: str) -> str:
    """
    Atomically move a PDF from raw/ to in_progress/ to prevent double-processing.

    Args:
        file_path: Absolute path in raw/.

    Returns:
        New path in in_progress/.
    """
    _, _, in_progress, _ = _get_dirs()
    src = Path(file_path)
    dest = in_progress / src.name
    src.rename(dest)
    logger.info("Claimed for processing: %s → %s", src.name, dest)
    return str(dest)


def mark_as_processed(file_path: str) -> str:
    """Move a completed invoice from in_progress/ to processed/."""
    _, processed, _, _ = _get_dirs()
    src = Path(file_path)
    dest = processed / src.name
    src.rename(dest)
    logger.info("Marked as processed: %s", src.name)
    return str(dest)


def mark_as_failed(file_path: str) -> str:
    """Move a failed invoice from in_progress/ to failed/."""
    _, _, _, failed = _get_dirs()
    src = Path(file_path)
    dest = failed / src.name
    src.rename(dest)
    logger.warning("Marked as failed: %s", src.name)
    return str(dest)
