# Minimal Dockerfile for File Service (Django)
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# System dependencies for building psycopg2 and health check tools
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    postgresql-client build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first for better caching
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

# Copy project
COPY . .

# Use entrypoint to wait for DB, run migrations, then start gunicorn
CMD ["sh", "/app/entrypoint.sh"]

