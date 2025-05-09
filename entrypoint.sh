#!/usr/bin/env bash
set -e

: "${DATABASE_PORT:=5432}"

echo "⏳ Waiting for database at $DATABASE_HOST:$DATABASE_PORT…"
while ! nc -z "$DATABASE_HOST" "$DATABASE_PORT"; do
  sleep 0.5
done
echo "✅ Database is up!"

echo "🔄 Applying Django migrations…"
python manage.py migrate --noinput

if [ "$DJANGO_ENV" = "production" ]; then
  echo "📦 Collecting static files…"
  python manage.py collectstatic --noinput
fi

echo "🚀 Starting server…"
exec uvicorn backend_sparqhub_django.asgi:application --host 0.0.0.0 --port 8000
