"""
database/models.py
SQLAlchemy ORM models for the invoice data warehouse.

Changes from v1:
- Added `file_hash` (SHA-256) column for exact-match deduplication.
  Replaces the LIKE-based duplicate check that caused false positives.
- Added `processing_status` column to track pipeline state per invoice.
  Failed invoices are now visible in the DB rather than disappearing silently.
- Dates stored as proper Date columns (not VARCHAR) for correct sorting/filtering.
- Removed get_engine() / get_session_factory() / create_tables() — these now live
  in database/session.py (singleton) and managed by Alembic migrations.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import (
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Invoice(Base):
    """Represents a processed invoice in the data warehouse."""

    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("file_hash", name="uq_invoices_file_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # ── Pipeline tracking ───────────────────────────────────────────────────
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    processing_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        index=True,
        # Values: pending | ocr_failed | extraction_failed | complete
    )
    processing_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Extracted fields ────────────────────────────────────────────────────
    invoice_number: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    vendor: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    invoice_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    total_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    tax_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="USD")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # ── Timestamps ──────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    line_items: Mapped[List["LineItem"]] = relationship(
        "LineItem",
        back_populates="invoice",
        cascade="all, delete-orphan",
        lazy="select",
    )

    def __repr__(self) -> str:
        return (
            f"<Invoice id={self.id} number={self.invoice_number!r} "
            f"vendor={self.vendor!r} status={self.processing_status!r}>"
        )


class LineItem(Base):
    """Represents a single line item on an invoice."""

    __tablename__ = "line_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    quantity: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    unit_price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    invoice: Mapped["Invoice"] = relationship("Invoice", back_populates="line_items")

    def __repr__(self) -> str:
        return f"<LineItem id={self.id} invoice_id={self.invoice_id} desc={self.description!r}>"
