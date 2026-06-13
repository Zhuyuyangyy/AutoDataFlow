# ============================================================
# AutoDataFlow v3.0 - Multi-stage Docker Build
# ============================================================
# Stage 1: Builder (install dependencies)
FROM python:3.11-slim AS builder

WORKDIR /build

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ============================================================
# Stage 2: Runtime (minimal image)
# ============================================================
FROM python:3.11-slim

# Security: run as non-root user
RUN groupadd -r autodataflow && useradd -r -g autodataflow -d /app autodataflow

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY input_data/ ./input_data/
COPY start.sh .

# Create data directories with proper permissions
RUN mkdir -p backend/data/logs backend/data/reports backend/config \
    && chown -R autodataflow:autodataflow /app

# Install curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Environment variables
ENV ADF_DATA_DIR="./backend/data" \
    ADF_PORT=8080 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AUTODATAFLOW_API_KEY="dev-key-change-me" \
    GUNICORN_WORKERS=4 \
    GUNICORN_THREADS=2 \
    GUNICORN_TIMEOUT=60

EXPOSE 8080

USER autodataflow

# Health check
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=15s \
    CMD curl -f http://localhost:8080/health || exit 1

# Run analysis then start the API server
CMD ["sh", "-c", "cd backend && python auto_data_flow.py && gunicorn app:app -c gunicorn_conf.py"]
