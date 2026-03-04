"""
config/settings.py
Centralised, validated configuration using pydantic-settings.

FIX: Previously config was constructed at import time with os.getenv() calls,
silently using defaults if .env wasn't loaded. pydantic-settings reads the .env
file automatically and raises a clear ValidationError on startup if required
fields are missing.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── PostgreSQL ────────────────────────────────────────────
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "invoice_db"
    postgres_user: str = "invoice_user"
    postgres_password: str = Field(..., description="Required: PostgreSQL password")

    # ── LLM ──────────────────────────────────────────────────
    llm_provider: Literal["openai", "local"] = "openai"
    llm_model: str = "gpt-3.5-turbo"
    openai_api_key: str = ""
    local_llm_url: str = "http://localhost:11434"

    # ── App ───────────────────────────────────────────────────
    log_level: str = "INFO"
    max_upload_size_mb: int = 50
    ocr_dpi: int = 300

    # ── File paths ────────────────────────────────────────────
    data_dir: str = "/app/data"
    invoices_raw_dir: str = "/app/data/invoices/raw"
    invoices_inprogress_dir: str = "/app/data/invoices/in_progress"  # FIX: staging dir
    invoices_processed_dir: str = "/app/data/invoices/processed"
    invoices_failed_dir: str = "/app/data/invoices/failed"           # FIX: failure tracking
    ocr_output_dir: str = "/app/data/ocr"
    extracted_output_dir: str = "/app/data/extracted"

    @model_validator(mode="after")
    def validate_llm_config(self) -> "Settings":
        if self.llm_provider == "openai" and not self.openai_api_key:
            raise ValueError(
                "OPENAI_API_KEY must be set when LLM_PROVIDER=openai. "
                "Either set the key or switch to LLM_PROVIDER=local."
            )
        return self

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return the cached Settings singleton.
    Using lru_cache means the .env file is read exactly once per process.
    Tests can call get_settings.cache_clear() to reset between test cases.
    """
    return Settings()
