"""
tests/unit/test_ocr_processor.py
Unit tests for the OCR service — mocks Tesseract and pdf2image.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch


@pytest.fixture(autouse=True)
def patch_settings(tmp_path, monkeypatch):
    """Override settings to use tmp_path so tests never touch /app/data."""
    monkeypatch.setenv("POSTGRES_PASSWORD", "test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OCR_OUTPUT_DIR", str(tmp_path / "ocr"))
    monkeypatch.setenv("INVOICES_RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("INVOICES_PROCESSED_DIR", str(tmp_path / "processed"))
    monkeypatch.setenv("INVOICES_INPROGRESS_DIR", str(tmp_path / "in_progress"))
    monkeypatch.setenv("INVOICES_FAILED_DIR", str(tmp_path / "failed"))
    monkeypatch.setenv("EXTRACTED_OUTPUT_DIR", str(tmp_path / "extracted"))
    # Clear settings cache so new env vars take effect
    from config.settings import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _make_fake_pdf(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.4 fake content")


class TestCleanTextBlocks:
    def test_filters_empty_lines(self):
        from services.ocr_service.ocr_processor import clean_text_blocks
        result = clean_text_blocks("line1\n\n  \nline2\n")
        assert result == ["line1", "line2"]

    def test_strips_whitespace(self):
        from services.ocr_service.ocr_processor import clean_text_blocks
        result = clean_text_blocks("  hello world  ")
        assert result == ["hello world"]

    def test_empty_input(self):
        from services.ocr_service.ocr_processor import clean_text_blocks
        assert clean_text_blocks("") == []


class TestOcrOutputPath:
    def test_path_uses_stem(self, tmp_path):
        from services.ocr_service.ocr_processor import get_ocr_output_path
        path = get_ocr_output_path("invoice_123.pdf")
        assert path.name == "invoice_123_ocr.json"

    def test_ocr_result_not_exists(self, tmp_path):
        from services.ocr_service.ocr_processor import ocr_result_exists
        assert not ocr_result_exists("nonexistent.pdf")


class TestProcessInvoicePdf:
    @patch("services.ocr_service.ocr_processor.convert_from_path")
    @patch("services.ocr_service.ocr_processor.pytesseract.image_to_string")
    def test_happy_path_saves_json(self, mock_ocr, mock_convert, tmp_path):
        from services.ocr_service.ocr_processor import process_invoice_pdf

        fake_image = MagicMock()
        mock_convert.return_value = [fake_image]
        mock_ocr.return_value = "Invoice Number: 1234\nVendor: ACME\nTotal: $500"

        pdf_path = tmp_path / "raw" / "test.pdf"
        _make_fake_pdf(pdf_path)

        result_path = process_invoice_pdf(str(pdf_path))

        assert Path(result_path).exists()
        data = json.loads(Path(result_path).read_text())
        assert data["file_name"] == "test.pdf"
        assert "Invoice Number: 1234" in data["text_blocks"]
        assert data["page_count"] == 1

    def test_raises_on_missing_file(self):
        from services.ocr_service.ocr_processor import process_invoice_pdf
        with pytest.raises(FileNotFoundError):
            process_invoice_pdf("/nonexistent/path/invoice.pdf")
