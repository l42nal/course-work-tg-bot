#!/usr/bin/env sh
set -e

echo "[entrypoint] Applying Alembic migrations..."
alembic upgrade head

echo "[entrypoint] Starting bot..."
exec python -m bot.main
