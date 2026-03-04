"""Initial schema — invoices and line_items tables.

Revision ID: 0001_initial_schema
Revises: 
Create Date: 2024-01-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invoices",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("file_path", sa.Text, nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("processing_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("processing_error", sa.Text, nullable=True),
        sa.Column("invoice_number", sa.String(100), nullable=True),
        sa.Column("vendor", sa.String(255), nullable=True),
        sa.Column("invoice_date", sa.Date, nullable=True),
        sa.Column("due_date", sa.Date, nullable=True),
        sa.Column("total_amount", sa.Float, nullable=False, server_default="0"),
        sa.Column("tax_amount", sa.Float, nullable=False, server_default="0"),
        sa.Column("currency", sa.String(10), nullable=False, server_default="USD"),
        sa.Column("confidence", sa.Float, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("file_hash", name="uq_invoices_file_hash"),
    )
    op.create_index("ix_invoices_file_hash", "invoices", ["file_hash"])
    op.create_index("ix_invoices_vendor", "invoices", ["vendor"])
    op.create_index("ix_invoices_invoice_number", "invoices", ["invoice_number"])
    op.create_index("ix_invoices_processing_status", "invoices", ["processing_status"])
    op.create_index("ix_invoices_invoice_date", "invoices", ["invoice_date"])

    op.create_table(
        "line_items",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "invoice_id",
            sa.Integer,
            sa.ForeignKey("invoices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("quantity", sa.Float, nullable=False, server_default="1"),
        sa.Column("unit_price", sa.Float, nullable=False, server_default="0"),
        sa.Column("total", sa.Float, nullable=False, server_default="0"),
    )
    op.create_index("ix_line_items_invoice_id", "line_items", ["invoice_id"])


def downgrade() -> None:
    op.drop_table("line_items")
    op.drop_table("invoices")
