#!/bin/sh
# =============================================================================
# Container entrypoint — wait for DB, run migrations, start the given command
# =============================================================================
set -e

echo "[entrypoint] Waiting for database…"
until python -c "
import os, sys, psycopg
url = os.environ.get('DATABASE_URL', '')
try:
    conn = psycopg.connect(url, connect_timeout=5)
    conn.close()
except Exception as e:
    print(f'  not ready: {e}', file=sys.stderr); sys.exit(1)
" 2>/dev/null; do
    sleep 2
done
echo "[entrypoint] Database is ready."

echo "[entrypoint] Running migrations…"
python manage.py migrate --noinput

echo "[entrypoint] Seeding tier configs (idempotent)…"
python manage.py seed_tiers

if [ -n "$DJANGO_SUPERUSER_EMAIL" ] && [ -n "$DJANGO_SUPERUSER_PASSWORD" ]; then
    echo "[entrypoint] Ensuring superuser exists…"
    python manage.py createsuperuser --noinput \
        --email "$DJANGO_SUPERUSER_EMAIL" || true
fi

echo "[entrypoint] Collecting static files…"
python manage.py collectstatic --noinput || true

echo "[entrypoint] Starting: $@"
exec "$@"
