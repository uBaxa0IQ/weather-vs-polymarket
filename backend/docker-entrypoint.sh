#!/bin/sh
set -e
cd /app
# DB is ready (depends_on healthcheck on db in compose). Apply Alembic before app starts.
alembic -c /app/alembic.ini upgrade head
exec "$@"
