# 1. Base image
FROM python:3.10-slim

# 2. Install system dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends netcat-openbsd curl \
    && rm -rf /var/lib/apt/lists/*

# 3. Create non-root user
RUN addgroup --system app && adduser --system --ingroup app app

# 4. Environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 5. Working directory
WORKDIR /app

# 6. Install Python dependencies
COPY requirements.in requirements.txt ./
RUN pip install --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# 7. Copy application code
COPY . .

# 8. Entrypoint
RUN chmod +x entrypoint.sh
ENTRYPOINT ["./entrypoint.sh"]

# 9. Switch to non-root user
USER app

# 10. Expose port and default command
EXPOSE 8000
CMD ["uvicorn", "backend_sparqhub_django.asgi:application", "--host", "0.0.0.0"]
