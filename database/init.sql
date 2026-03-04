-- ============================================================
-- Invoice AI Pipeline — PostgreSQL Bootstrap
--
-- This file runs ONCE when the Postgres container first starts.
-- It only creates the database role and extension.
--
-- FIX: Previously this file also defined tables, duplicating the
-- SQLAlchemy models and diverging over time. Table creation is
-- now handled exclusively by Alembic migrations (alembic upgrade head)
-- which runs via docker/entrypoint.airflow.sh on every container start.
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";  -- enables fast LIKE/similarity search

-- The database and user are created by Docker's POSTGRES_DB / POSTGRES_USER env vars.
-- Grant any additional privileges here if needed.
