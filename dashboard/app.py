"""
dashboard/app.py
Streamlit analytics dashboard for the Invoice AI Pipeline.

FIX from v1:
- Paginated queries with LIMIT/OFFSET — no more SELECT * loading all rows
  into memory which would OOM at scale.
- Shows failed invoices separately so pipeline health is visible.
- Date filter uses the indexed invoice_date column.
- Connection uses cached singleton engine from database/session.py.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import text

logger = logging.getLogger(__name__)

st.set_page_config(
    page_title="Invoice AI Pipeline",
    page_icon="🧾",
    layout="wide",
    initial_sidebar_state="expanded",
)

PAGE_SIZE = 500  # Maximum rows loaded per query


# ── DB helpers ─────────────────────────────────────────────────────────────────

@st.cache_resource
def _get_engine():
    """Reuse application singleton engine."""
    import sys
    sys.path.insert(0, "/app")
    from database.session import get_engine
    return get_engine()


@st.cache_data(ttl=30)
def load_invoices(
    vendor_filter: tuple[str, ...],
    date_from: str,
    date_to: str,
    min_confidence: float,
    status_filter: str,
    offset: int,
) -> pd.DataFrame:
    """
    Load a paginated, filtered page of invoices from PostgreSQL.

    FIX: LIMIT + OFFSET + indexed WHERE clauses replace unbounded SELECT *.
    """
    engine = _get_engine()
    conditions = ["1=1"]
    params: dict = {"limit": PAGE_SIZE, "offset": offset}

    if vendor_filter:
        conditions.append("vendor = ANY(:vendors)")
        params["vendors"] = list(vendor_filter)
    if date_from:
        conditions.append("invoice_date >= :date_from")
        params["date_from"] = date_from
    if date_to:
        conditions.append("invoice_date <= :date_to")
        params["date_to"] = date_to
    if min_confidence > 0:
        conditions.append("confidence >= :min_conf")
        params["min_conf"] = min_confidence
    if status_filter != "all":
        conditions.append("processing_status = :status")
        params["status"] = status_filter

    where = " AND ".join(conditions)
    query = f"""
        SELECT id, invoice_number, vendor, invoice_date, due_date,
               total_amount, tax_amount, currency, confidence,
               processing_status, processing_error, created_at
        FROM invoices
        WHERE {where}
        ORDER BY created_at DESC
        LIMIT :limit OFFSET :offset
    """
    try:
        with engine.connect() as conn:
            return pd.read_sql(text(query), conn, params=params)
    except Exception as exc:
        logger.error("Failed to load invoices: %s", exc)
        return pd.DataFrame()


@st.cache_data(ttl=30)
def load_kpis() -> dict:
    """Aggregate KPIs — runs a single efficient query."""
    engine = _get_engine()
    query = """
        SELECT
            COUNT(*) FILTER (WHERE processing_status = 'complete')    AS total_processed,
            COUNT(*) FILTER (WHERE processing_status LIKE '%failed%') AS total_failed,
            COUNT(*) FILTER (WHERE processing_status = 'pending')     AS total_pending,
            COALESCE(SUM(total_amount) FILTER (WHERE processing_status = 'complete'), 0) AS total_spend,
            COALESCE(AVG(confidence)  FILTER (WHERE processing_status = 'complete'), 0) AS avg_confidence,
            COUNT(DISTINCT vendor)    FILTER (WHERE processing_status = 'complete') AS unique_vendors
        FROM invoices
    """
    try:
        with engine.connect() as conn:
            row = conn.execute(text(query)).fetchone()
            return dict(row._mapping) if row else {}
    except Exception as exc:
        logger.error("KPI query failed: %s", exc)
        return {}


@st.cache_data(ttl=60)
def load_vendor_spend() -> pd.DataFrame:
    engine = _get_engine()
    query = """
        SELECT vendor, SUM(total_amount) AS total_spend, COUNT(*) AS invoice_count
        FROM invoices
        WHERE processing_status = 'complete' AND vendor IS NOT NULL
        GROUP BY vendor
        ORDER BY total_spend DESC
        LIMIT 20
    """
    try:
        with engine.connect() as conn:
            return pd.read_sql(text(query), conn)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=60)
def load_daily_volume() -> pd.DataFrame:
    engine = _get_engine()
    query = """
        SELECT DATE(created_at) AS day, COUNT(*) AS count,
               SUM(total_amount) AS daily_spend
        FROM invoices
        WHERE processing_status = 'complete'
        GROUP BY DATE(created_at)
        ORDER BY day
    """
    try:
        with engine.connect() as conn:
            return pd.read_sql(text(query), conn)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=60)
def load_confidence_dist() -> pd.DataFrame:
    engine = _get_engine()
    query = """
        SELECT confidence FROM invoices WHERE processing_status = 'complete'
    """
    try:
        with engine.connect() as conn:
            return pd.read_sql(text(query), conn)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=30)
def load_all_vendors() -> list[str]:
    engine = _get_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT DISTINCT vendor FROM invoices WHERE vendor IS NOT NULL ORDER BY vendor")
            ).fetchall()
            return [r[0] for r in rows]
    except Exception:
        return []


# ── Sidebar ────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("🧾 Invoice AI Pipeline")
    st.caption("Automated invoice processing analytics")
    st.divider()

    if st.button("🔄 Refresh"):
        st.cache_data.clear()
        st.rerun()

    st.markdown("#### Filters")
    vendors = load_all_vendors()
    vendor_sel = st.multiselect("Vendor", options=vendors)
    date_from = st.date_input("From date", value=date.today() - timedelta(days=365))
    date_to = st.date_input("To date", value=date.today())
    min_conf = st.slider("Min confidence", 0.0, 1.0, 0.0, 0.05)
    status_sel = st.selectbox(
        "Status", ["all", "complete", "pending", "ocr_failed", "extraction_failed"]
    )
    page = st.number_input("Page", min_value=1, value=1, step=1)

    st.divider()
    st.markdown("**Service Links**")
    st.markdown("[Upload API](http://localhost:8000/docs) · [Airflow](http://localhost:8080)")
    st.caption(f"Refreshed: {datetime.now().strftime('%H:%M:%S')}")

# ── KPIs ───────────────────────────────────────────────────────────────────────

st.title("🧾 Invoice AI Pipeline — Dashboard")
kpis = load_kpis()

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("✅ Processed", kpis.get("total_processed", 0))
c2.metric("⏳ Pending", kpis.get("total_pending", 0))
c3.metric("❌ Failed", kpis.get("total_failed", 0))
c4.metric("💰 Total Spend", f"${kpis.get('total_spend', 0):,.0f}")
c5.metric("🎯 Avg Confidence", f"{kpis.get('avg_confidence', 0):.1%}")

st.divider()

# ── Charts ─────────────────────────────────────────────────────────────────────

df_vendor = load_vendor_spend()
df_daily = load_daily_volume()
df_conf = load_confidence_dist()

row1a, row1b = st.columns(2)

with row1a:
    st.subheader("💼 Vendor Spending (Top 20)")
    if not df_vendor.empty:
        fig = px.bar(
            df_vendor, x="vendor", y="total_spend",
            color="total_spend", color_continuous_scale="Blues",
            labels={"vendor": "", "total_spend": "Total ($)"},
        )
        fig.update_layout(xaxis_tickangle=-40, coloraxis_showscale=False, margin=dict(t=10, b=80))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No vendor data yet.")

with row1b:
    st.subheader("🎯 Confidence Distribution")
    if not df_conf.empty:
        mean_conf = df_conf["confidence"].mean()
        fig = px.histogram(df_conf, x="confidence", nbins=20, range_x=[0, 1],
                           color_discrete_sequence=["#4C8BF5"])
        fig.add_vline(x=mean_conf, line_dash="dash", line_color="orange",
                      annotation_text=f"Mean {mean_conf:.2f}")
        fig.update_layout(margin=dict(t=10))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No confidence data yet.")

row2a, row2b = st.columns(2)

with row2a:
    st.subheader("📅 Daily Invoice Volume")
    if not df_daily.empty:
        fig = px.line(df_daily, x="day", y="count", markers=True,
                      color_discrete_sequence=["#4C8BF5"])
        fig.update_layout(margin=dict(t=10))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No timeline data yet.")

with row2b:
    st.subheader("💵 Daily Spend")
    if not df_daily.empty:
        fig = px.area(df_daily, x="day", y="daily_spend",
                      color_discrete_sequence=["#34A853"])
        fig.update_layout(margin=dict(t=10))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No spend data yet.")

st.divider()

# ── Invoice Table (paginated) ──────────────────────────────────────────────────

st.subheader(f"📋 Invoices (page {page}, up to {PAGE_SIZE} rows)")

df = load_invoices(
    vendor_filter=tuple(vendor_sel),
    date_from=str(date_from),
    date_to=str(date_to),
    min_confidence=min_conf,
    status_filter=status_sel,
    offset=(page - 1) * PAGE_SIZE,
)

if df.empty:
    st.info("No invoices match the current filters.")
else:
    status_colors = {
        "complete": "🟢", "pending": "🟡",
        "ocr_failed": "🔴", "extraction_failed": "🔴",
    }
    df["status_label"] = df["processing_status"].map(
        lambda s: f"{status_colors.get(s, '⚪')} {s}"
    )

    display_cols = {
        "id": "ID", "invoice_number": "Invoice #", "vendor": "Vendor",
        "invoice_date": "Date", "total_amount": "Total ($)",
        "tax_amount": "Tax ($)", "currency": "Curr",
        "confidence": "Confidence", "status_label": "Status", "created_at": "Processed",
    }
    st.dataframe(
        df[list(display_cols.keys())].rename(columns=display_cols),
        use_container_width=True,
        hide_index=True,
    )
    st.caption(f"Showing {len(df)} invoice(s). Use page control in sidebar for more.")

    # Failed invoice details
    failed = df[df["processing_status"].str.contains("failed", na=False)]
    if not failed.empty:
        with st.expander(f"❌ Failed Invoices ({len(failed)})"):
            st.dataframe(
                failed[["id", "processing_status", "processing_error", "created_at"]],
                use_container_width=True,
                hide_index=True,
            )

st.divider()
st.caption("Invoice AI Pipeline v2 · FastAPI · Airflow · Tesseract · PostgreSQL · Streamlit")
