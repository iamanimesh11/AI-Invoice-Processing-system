"""
scripts/seed_demo_data.py
Inserts synthetic invoice rows into PostgreSQL for dashboard smoke-testing.
Run inside any container that has database/ on its PYTHONPATH.
"""

import random
import sys
import os
from datetime import date, timedelta

sys.path.insert(0, "/app")

from database.session import get_db_session
from database.models import Invoice, LineItem

VENDORS = [
    "Acme Supplies Ltd", "Global Tech Corp", "Swift Logistics",
    "DataSoft Solutions", "Prime Office Goods", "Vertex Consulting",
    "NorthStar Media", "Reliable Parts Co", "CloudNine Services", "BrightPath Inc",
]

ITEMS = [
    ("Software License", 1, 499.00),
    ("Cloud Hosting (monthly)", 1, 199.00),
    ("Consulting Hours", 8, 150.00),
    ("Office Supplies", 10, 12.50),
    ("Hardware Maintenance", 1, 350.00),
    ("Data Storage (TB)", 5, 45.00),
    ("Training Workshop", 2, 800.00),
    ("Shipping & Handling", 1, 25.00),
]


def seed(n: int = 50) -> None:
    print(f"Seeding {n} demo invoices...")
    with get_db_session() as session:
        for i in range(1, n + 1):
            vendor = random.choice(VENDORS)
            inv_date = date(2024, 1, 1) + timedelta(days=random.randint(0, 365))
            due_date = inv_date + timedelta(days=30)
            items = random.sample(ITEMS, k=random.randint(1, 4))
            subtotal = sum(qty * price for _, qty, price in items)
            tax = round(subtotal * 0.1, 2)
            total = round(subtotal + tax, 2)

            inv = Invoice(
                invoice_number=f"DEMO-{i:05d}",
                vendor=vendor,
                invoice_date=str(inv_date),
                due_date=str(due_date),
                total_amount=total,
                tax_amount=tax,
                currency=random.choice(["USD", "USD", "USD", "EUR", "GBP"]),
                confidence=round(random.uniform(0.70, 0.99), 2),
                file_path=f"data/invoices/processed/demo_{i:05d}.pdf",
                processing_status="complete",
            )
            session.add(inv)
            session.flush()

            for desc, qty, unit_price in items:
                session.add(LineItem(
                    invoice_id=inv.id,
                    description=desc,
                    quantity=float(qty),
                    unit_price=unit_price,
                    total=round(qty * unit_price, 2),
                ))

        session.commit()
    print(f"Done. {n} invoices and their line items inserted.")


if __name__ == "__main__":
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    seed(count)
