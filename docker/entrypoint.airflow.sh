#!/usr/bin/env bash
# docker/entrypoint.airflow.sh
# Runs before the Airflow scheduler/webserver starts.
# Applies any pending Alembic migrations so schema is always up to date.
set -euo pipefail

echo "[entrypoint] Running Alembic migrations..."
cd /app
alembic upgrade head
echo "[entrypoint] Migrations complete."

# Hand off to the Airflow command (scheduler / webserver / etc.)
exec airflow "$@"
