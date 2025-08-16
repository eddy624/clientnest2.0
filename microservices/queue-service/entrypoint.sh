#!/bin/sh
set -e

# Wait for DB
until pg_isready -h "$POSTGRES_DB_HOST" -p "$POSTGRES_DB_PORT" -U "$POSTGRES_DB_USER"; do
  echo "Waiting for database at $POSTGRES_DB_HOST:$POSTGRES_DB_PORT...";
  sleep 1;
done

echo "DB ready. Running migrations..."
python manage.py migrate --noinput || true
python manage.py collectstatic --noinput || true

# Start server
exec gunicorn queue_service.wsgi:application --bind 0.0.0.0:8000 --workers 2 --timeout 120

