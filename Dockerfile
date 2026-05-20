# syntax=docker/dockerfile:1.7
# =============================================================================
# Labor Law Partner — Django backend production image
# =============================================================================
# Multi-stage build: a slim runtime with no build tools or pip cache.
# Targets python:3.12-slim-bookworm for x86_64 (works on AWS Fargate Linux/x86).

FROM python:3.12-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Build deps for psycopg, argon2-cffi, cryptography, etc.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt


# ── Runtime stage ───────────────────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    DJANGO_SETTINGS_MODULE=config.settings.prod \
    PATH=/home/llp/.local/bin:$PATH

# Runtime libs only — no compilers
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    libmagic1 \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash --uid 1000 llp

# Copy installed packages from builder
COPY --from=builder /root/.local /home/llp/.local
RUN chown -R llp:llp /home/llp/.local

WORKDIR /app
COPY --chown=llp:llp . /app
RUN mkdir -p /app/staticfiles && chown -R llp:llp /app/staticfiles

USER llp

# Collectstatic at build time (no DB needed for STATIC_ROOT)
RUN SECRET_KEY=build python manage.py collectstatic --noinput || true

EXPOSE 8000

# Default: gunicorn for sync requests + ASGI for streaming via uvicorn workers
# Override via docker-compose / ECS task definition for `celery worker`, etc.
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--timeout", "300"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8000/api/health/ || exit 1
