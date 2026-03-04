"""
services/upload_service/main.py
FastAPI application entry point for the invoice upload service.
"""

from __future__ import annotations

import logging
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

sys.path.insert(0, "/app")

from services.upload_service.routes import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Invoice AI Pipeline — Upload Service",
    description=(
        "Accepts PDF invoice uploads, validates file content, saves to local storage, "
        "and registers a pending pipeline record in PostgreSQL."
    ),
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/health", tags=["Ops"])
async def health_check() -> dict:
    """Liveness probe for Docker and load balancers."""
    return {"status": "healthy", "service": "upload_service", "version": "2.0.0"}


@app.on_event("startup")
async def on_startup() -> None:
    from config.settings import get_settings
    from services.upload_service.storage import ensure_directories
    settings = get_settings()
    ensure_directories()
    logger.info(
        "Upload service started | max_upload=%dMB | llm_provider=%s",
        settings.max_upload_size_mb,
        settings.llm_provider,
    )
