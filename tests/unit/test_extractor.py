"""
tests/unit/test_extractor.py
Unit tests for the LLM extraction service.
"""

import json
import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def patch_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", "test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("EXTRACTED_OUTPUT_DIR", str(tmp_path / "extracted"))
    monkeypatch.setenv("INVOICES_RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("INVOICES_INPROGRESS_DIR", str(tmp_path / "in_progress"))
    monkeypatch.setenv("INVOICES_PROCESSED_DIR", str(tmp_path / "processed"))
    monkeypatch.setenv("INVOICES_FAILED_DIR", str(tmp_path / "failed"))
    monkeypatch.setenv("OCR_OUTPUT_DIR", str(tmp_path / "ocr"))
    from config.settings import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


GOOD_LLM_RESPONSE = json.dumps({
    "invoice_number": "INV-9999",
    "vendor": "Test Corp",
    "invoice_date": "2024-03-15",
    "due_date": "2024-04-15",
    "total_amount": 1200.0,
    "tax_amount": 100.0,
    "currency": "USD",
    "confidence": 0.95,
    "line_items": [
        {"description": "Consulting", "quantity": 8, "unit_price": 137.5, "total": 1100.0}
    ],
})


class TestParseJsonResponse:
    def test_parses_clean_json(self):
        from services.extraction_service.extractor import _parse_json_response
        data = _parse_json_response(GOOD_LLM_RESPONSE)
        assert data["invoice_number"] == "INV-9999"
        assert data["total_amount"] == 1200.0

    def test_strips_markdown_fences(self):
        from services.extraction_service.extractor import _parse_json_response
        fenced = f"```json\n{GOOD_LLM_RESPONSE}\n```"
        data = _parse_json_response(fenced)
        assert data["vendor"] == "Test Corp"

    def test_raises_on_garbage(self):
        from services.extraction_service.extractor import _parse_json_response, ExtractionError
        with pytest.raises(ExtractionError):
            _parse_json_response("This is not JSON at all.")


class TestNormalise:
    def test_fills_missing_fields_with_defaults(self):
        from services.extraction_service.extractor import _normalise
        result = _normalise({"vendor": "ACME"})
        assert result["total_amount"] == 0.0
        assert result["currency"] == "USD"
        assert result["line_items"] == []

    def test_coerces_string_amounts(self):
        from services.extraction_service.extractor import _normalise
        result = _normalise({"total_amount": "1500.50", "tax_amount": None})
        assert result["total_amount"] == 1500.50
        assert result["tax_amount"] == 0.0


class TestExtractInvoiceFields:
    @patch("services.extraction_service.extractor.call_llm", return_value=GOOD_LLM_RESPONSE)
    def test_saves_json_and_returns_path(self, mock_llm, tmp_path):
        from services.extraction_service.extractor import extract_invoice_fields
        from pathlib import Path

        output_path = extract_invoice_fields(
            ocr_text="Invoice Number: INV-9999\nVendor: Test Corp",
            pdf_name="test_invoice.pdf",
            file_path="/app/data/invoices/raw/test_invoice.pdf",
        )

        assert Path(output_path).exists()
        data = json.loads(Path(output_path).read_text())
        assert data["invoice_number"] == "INV-9999"
        assert data["source_file"] == "test_invoice.pdf"

    @patch("services.extraction_service.extractor.call_llm", return_value="not json")
    def test_raises_extraction_error_on_bad_llm_response(self, mock_llm):
        from services.extraction_service.extractor import extract_invoice_fields, ExtractionError
        with pytest.raises(ExtractionError):
            extract_invoice_fields("some text", "invoice.pdf")
