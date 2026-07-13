#!/bin/sh
set -e

# Wait for Postgres if configured (poll DATABASE_URL host:port)
if [ -n "$PLATFORM_DATABASE_URL" ]; then
    echo "[nexus-platform] Running Alembic migrations..."
    alembic upgrade head || {
        echo "[nexus-platform] Alembic failed — continuing (tables may be created lazily)"
    }
fi

echo "[nexus-platform] Starting: $@"
exec "$@"
