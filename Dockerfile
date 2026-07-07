FROM python:3.10-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

RUN addgroup --system app && adduser --system --ingroup app app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chown -R app:app /app

# collectstatic only needs Django settings to import cleanly — it never
# touches the database or does anything security-sensitive with SECRET_KEY.
# These placeholders are overridden by the platform's real runtime env vars
# when the container actually starts; they're never used for anything else.
ENV SECRET_KEY=build-time-placeholder-unused-at-runtime \
    DATABASE_PASSWORD=build-time-placeholder \
    ALLOWED_HOSTS=build-time-placeholder \
    CORS_ALLOWED_ORIGINS=http://build-time-placeholder \
    FIELD_ENCRYPTION_KEY=5SSo0na4r6mDByBidbXkzkHvYtfud6xAep5dUKtEQcc=
RUN python manage.py collectstatic --noinput

USER app

EXPOSE 8000
CMD ["sh", "-c", "uvicorn backend_sparqhub_django.asgi:application --host 0.0.0.0 --port ${PORT:-8000}"]
