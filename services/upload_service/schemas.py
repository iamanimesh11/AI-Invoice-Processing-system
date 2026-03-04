"""
services/upload_service/schemas.py
Pydantic request and response models for the upload service API.

Previously missing — without these, FastAPI generates untyped responses
and the OpenAPI docs show no schema for the /upload-invoice endpoint.
"""

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class UploadResponse(BaseModel):
    """Response returned after a successful invoice upload."""
    status: str = Field(..., examples=["uploaded"])
    file_id: str = Field(..., description="UUID identifying this upload")
    file_hash: str = Field(..., description="SHA-256 of the uploaded file (for deduplication)")
    original_filename: str
    file_path: str = Field(..., description="Relative path where the file was stored")
    message: str


class InvoiceListItem(BaseModel):
    """Metadata for a single invoice in the raw directory listing."""
    filename: str
    path: str
    size_bytes: int
    modified_at: str
    file_hash: Optional[str] = None


class InvoiceListResponse(BaseModel):
    """Response for GET /invoices."""
    count: int
    invoices: list[InvoiceListItem]


class ErrorResponse(BaseModel):
    """Standard error envelope."""
    detail: str
