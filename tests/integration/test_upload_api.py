"""
tests/integration/test_upload_api.py
Integration tests for the FastAPI upload service.
Uses httpx.AsyncClient — no real DB or file system (fully mocked).
"""

import io
import pytest
import pytest_asyncio
from unittest.mock import patch, MagicMock


@pytest.fixture(autouse=True)
def patch_env(monkeypatch, tmp_path):
    monkeypatch.setenv("POSTGRES_PASSWORD", "test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("INVOICES_RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("INVOICES_INPROGRESS_DIR", str(tmp_path / "in_progress"))
    monkeypatch.setenv("INVOICES_PROCESSED_DIR", str(tmp_path / "processed"))
    monkeypatch.setenv("INVOICES_FAILED_DIR", str(tmp_path / "failed"))
    monkeypatch.setenv("OCR_OUTPUT_DIR", str(tmp_path / "ocr"))
    monkeypatch.setenv("EXTRACTED_OUTPUT_DIR", str(tmp_path / "extracted"))
    from config.settings import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client():
    from httpx import Client
    from services.upload_service.main import app
    with Client(app=app, base_url="http://test") as c:
        yield c


def _pdf_upload(filename: str = "test.pdf") -> dict:
    return {"file": (filename, io.BytesIO(b"%PDF-1.4 test content"), "application/pdf")}


class TestHealthCheck:
    def test_returns_healthy(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"


class TestUploadInvoice:
    @patch("services.upload_service.routes.register_invoice_pending", return_value=1)
    @patch("services.upload_service.routes.invoice_already_processed", return_value=False)
    def test_valid_pdf_upload_returns_201(self, mock_dup, mock_reg, client):
        resp = client.post("/api/v1/upload-invoice", files=_pdf_upload())
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "uploaded"
        assert "file_hash" in body
        assert body["original_filename"] == "test.pdf"

    def test_non_pdf_rejected_with_400(self, client):
        resp = client.post(
            "/api/v1/upload-invoice",
            files={"file": ("report.docx", io.BytesIO(b"not a pdf"), "application/msword")},
        )
        assert resp.status_code == 400

    def test_empty_file_rejected_with_400(self, client):
        resp = client.post(
            "/api/v1/upload-invoice",
            files={"file": ("empty.pdf", io.BytesIO(b""), "application/pdf")},
        )
        assert resp.status_code == 400

    @patch("services.upload_service.routes.invoice_already_processed", return_value=True)
    def test_duplicate_file_rejected_with_409(self, mock_dup, client):
        resp = client.post("/api/v1/upload-invoice", files=_pdf_upload())
        assert resp.status_code == 409


class TestListInvoices:
    def test_returns_empty_list_when_no_files(self, client):
        resp = client.get("/api/v1/invoices")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0
        assert resp.json()["invoices"] == []
