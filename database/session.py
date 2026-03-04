"""
database/session.py
Singleton SQLAlchemy engine and session context manager.

FIX: Previously get_engine() was called inside every DB function, creating a new
engine (and connection pool) on each invocation. This exhausted PostgreSQL's
max_connections under concurrent Airflow task execution.

Solution: One engine per process, configured with NullPool for short-lived Airflow
workers so connections are closed immediately after use rather than pooled across tasks.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from functools import lru_cache
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool, QueuePool

logger = logging.getLogger(__name__)

# ── Engine factory ─────────────────────────────────────────────────────────────

def _is_airflow_worker() -> bool:
    """Detect if we're running inside an Airflow task worker process."""
    return bool(os.getenv("AIRFLOW_CTX_DAG_ID") or os.getenv("AIRFLOW__CORE__EXECUTOR"))


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """
    Return the process-level singleton SQLAlchemy engine.

    Uses NullPool for Airflow workers (short-lived processes that should not
    hold idle connections between tasks) and QueuePool for long-running services
    like the upload API and dashboard.
    """
    from config.settings import get_settings
    settings = get_settings()

    if _is_airflow_worker():
        # NullPool: connections are opened and closed per-operation.
        # Prevents connection exhaustion when many Airflow tasks run concurrently.
        pool_class = NullPool
        pool_kwargs: dict = {}
        logger.info("DB engine: NullPool (Airflow worker mode)")
    else:
        pool_class = QueuePool
        pool_kwargs = {
            "pool_size": 5,
            "max_overflow": 10,
            "pool_timeout": 30,
            "pool_recycle": 1800,
        }
        logger.info("DB engine: QueuePool size=5 (service mode)")

    engine = create_engine(
        settings.database_url,
        poolclass=pool_class,
        pool_pre_ping=True,   # Validates connections before use
        echo=False,
        **pool_kwargs,
    )

    @event.listens_for(engine, "connect")
    def set_search_path(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("SET search_path TO public")
        cursor.close()

    return engine


# ── Session factory ────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _get_session_factory() -> sessionmaker:
    return sessionmaker(
        bind=get_engine(),
        autoflush=True,
        autocommit=False,
        expire_on_commit=False,  # Avoids lazy-load errors after commit
    )


@contextmanager
def get_db_session() -> Generator[Session, None, None]:
    """
    Provide a transactional database session as a context manager.

    Usage:
        with get_db_session() as session:
            session.add(invoice)
            session.commit()

    Automatically rolls back on exception and closes the session on exit.
    """
    factory = _get_session_factory()
    session: Session = factory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
