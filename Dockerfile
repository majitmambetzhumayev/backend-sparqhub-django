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

RUN python manage.py collectstatic --noinput

USER app

EXPOSE 8000
CMD ["uvicorn", "backend_sparqhub_django.asgi:application", "--host", "0.0.0.0", "--port", "8000"]
