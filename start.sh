#!/bin/sh
# Container entry point: bring the database schema up to date, then serve.
set -e
alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}" \
  --proxy-headers --forwarded-allow-ips "*" --no-server-header
