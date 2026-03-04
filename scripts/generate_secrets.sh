#!/usr/bin/env bash
# ============================================================
# Generate cryptographic secrets required by the pipeline.
# Run once before first `docker compose up`.
# Usage: bash scripts/generate_secrets.sh
# ============================================================
set -euo pipefail

echo ""
echo "=== Invoice AI Pipeline — Secret Generator ==="
echo ""

FERNET_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
WEB_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
PG_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
AF_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(16))")

echo "Add these lines to your .env file:"
echo ""
echo "AIRFLOW__CORE__FERNET_KEY=${FERNET_KEY}"
echo "AIRFLOW__WEBSERVER__SECRET_KEY=${WEB_SECRET}"
echo "POSTGRES_PASSWORD=${PG_PASSWORD}"
echo "AIRFLOW_ADMIN_PASSWORD=${AF_PASSWORD}"
echo ""
echo "Or run this to append directly:"
echo ""
echo "  cat >> .env << 'EOF'"
echo "AIRFLOW__CORE__FERNET_KEY=${FERNET_KEY}"
echo "AIRFLOW__WEBSERVER__SECRET_KEY=${WEB_SECRET}"
echo "POSTGRES_PASSWORD=${PG_PASSWORD}"
echo "AIRFLOW_ADMIN_PASSWORD=${AF_PASSWORD}"
echo "EOF"
echo ""
