# API image (FastAPI + python-oracledb thin mode: no Oracle client libraries needed).
# Secrets are NOT baked in: pass them as environment variables when the container runs.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

# run as an unprivileged user; storage_sim is the SIMULATED cloud storage (ephemeral on free hosts)
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/storage_sim \
    && chown -R appuser /app
USER appuser

EXPOSE 8000

# Render (and most hosts) tell the container which port to use through $PORT.
CMD ["sh", "-c", "uvicorn app.main:create_app --factory --host 0.0.0.0 --port ${PORT:-8000}"]
