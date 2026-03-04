"""
services/ocr_service/ocr_processor.py
PDF → image → Tesseract OCR pipeline.

FIX from v1: Output is written to disk immediately so the Airflow DAG
only needs to pass file paths via XCom, not the full OCR text blobs.
This prevents XCom size limit violations on multi-page or batch runs.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List

import pytesseract
from pdf2image import convert_from_path
from PIL import Image

from config.settings import get_settings

logger = logging.getLogger(__name__)


def _ocr_output_dir() -> Path:
    return Path(get_settings().ocr_output_dir)


def _dpi() -> int:
    return get_settings().ocr_dpi


def ensure_ocr_dir() -> None:
    _ocr_output_dir().mkdir(parents=True, exist_ok=True)


def pdf_to_images(pdf_path: str) -> List[Image.Image]:
    """Convert a PDF to a list of PIL Images at configured DPI."""
    dpi = _dpi()
    logger.info("Converting PDF → images: %s (dpi=%d)", Path(pdf_path).name, dpi)
    images = convert_from_path(pdf_path, dpi=dpi)
    logger.info("Converted %d page(s)", len(images))
    return images


def extract_text_from_image(image: Image.Image, page_num: int = 0) -> str:
    """Run Tesseract on a single PIL Image and return cleaned text."""
    config = "--psm 6 --oem 3"
    text = pytesseract.image_to_string(image, config=config)
    return text.strip()


def clean_text_blocks(raw_text: str) -> List[str]:
    """Split OCR output into non-empty lines."""
    return [line.strip() for line in raw_text.splitlines() if line.strip()]


def get_ocr_output_path(pdf_name: str) -> Path:
    """Return the expected path for a PDF's OCR JSON output."""
    stem = Path(pdf_name).stem
    return _ocr_output_dir() / f"{stem}_ocr.json"


def ocr_result_exists(pdf_name: str) -> bool:
    """Return True if OCR has already been run for this PDF."""
    return get_ocr_output_path(pdf_name).exists()


def process_invoice_pdf(pdf_path: str) -> str:
    """
    Full OCR pipeline: PDF → images → text → JSON file on disk.

    FIX: Returns the *path* to the OCR JSON file rather than the full result dict.
    The Airflow task pushes this path via XCom instead of the raw text blob,
    keeping XCom payloads tiny (file path strings only).

    Args:
        pdf_path: Absolute path to the source PDF.

    Returns:
        Absolute path to the saved OCR JSON file.

    Raises:
        FileNotFoundError: If the PDF does not exist.
        RuntimeError: If Tesseract fails.
    """
    ensure_ocr_dir()
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"Invoice PDF not found: {pdf_path}")

    images = pdf_to_images(str(path))
    all_text_blocks: List[str] = []
    page_texts: List[Dict[str, Any]] = []

    for page_num, image in enumerate(images):
        raw_text = extract_text_from_image(image, page_num)
        blocks = clean_text_blocks(raw_text)
        all_text_blocks.extend(blocks)
        page_texts.append({"page": page_num + 1, "raw_text": raw_text, "line_count": len(blocks)})
        logger.debug("Page %d: %d text lines", page_num + 1, len(blocks))

    result: Dict[str, Any] = {
        "file_name": path.name,
        "file_path": str(path),
        "processed_at": datetime.utcnow().isoformat(),
        "page_count": len(images),
        "text_blocks": all_text_blocks,
        "pages": page_texts,
        "full_text": "\n".join(all_text_blocks),
    }

    output_path = get_ocr_output_path(path.name)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    logger.info(
        "OCR complete: %s → %s (%d blocks)", path.name, output_path.name, len(all_text_blocks)
    )
    return str(output_path)


def load_ocr_result(ocr_json_path: str) -> Dict[str, Any]:
    """Load a saved OCR JSON file from disk."""
    p = Path(ocr_json_path)
    if not p.exists():
        raise FileNotFoundError(f"OCR result not found: {ocr_json_path}")
    return json.loads(p.read_text())
