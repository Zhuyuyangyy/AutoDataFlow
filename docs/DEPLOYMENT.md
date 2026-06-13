# AutoDataFlow Deployment Guide

## Prerequisites

- Python 3.10+
- pip
- Docker (optional)

## Local Development

```bash
# Clone repository
git clone <repository-url>
cd AutoDataFlow

# Install dependencies
pip install -r requirements.txt

# Generate sample data and run analysis
cd backend
python auto_data_flow.py

# Start development server
python app.py
```

The API will be available at `http://localhost:8080`.

## Production Deployment

### Gunicorn (Recommended)

```bash
cd backend
gunicorn app:app -c gunicorn_conf.py
```

Configuration via environment variables:
- `GUNICORN_WORKERS=4` (default)
- `GUNICORN_THREADS=2` (default)
- `GUNICORN_TIMEOUT=60` (default)
- `GUNICORN_BIND=0.0.0.0:8080` (default)

### Docker

```bash
# Build image
docker build -t autodataflow .

# Run container
docker run -d \
  -p 8080:8080 \
  -e AUTODATAFLOW_API_KEY=your-secret-key \
  -v autodataflow_data:/app/data \
  --name autodataflow \
  autodataflow
```

### Docker Compose

```bash
# Start all services
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AUTODATAFLOW_API_KEY` | `dev-key-change-me` | API key for protected endpoints |
| `GUNICORN_WORKERS` | `4` | Number of worker processes |
| `GUNICORN_THREADS` | `2` | Threads per worker |
| `GUNICORN_TIMEOUT` | `60` | Request timeout in seconds |
| `GUNICORN_BIND` | `0.0.0.0:8080` | Bind address |
| `ADF_PORT` | `8080` | Service port |

## Health Check

```
GET /health
```

Returns `{"status": "ok"}` when the service is healthy.

## Monitoring

### Prometheus Metrics

```
GET /metrics
```

Available metrics:
- `autodataflow_http_requests_total` - Request count by method/endpoint/status
- `autodataflow_http_request_duration_seconds` - Request latency histogram
- `autodataflow_active_requests` - Active request gauge
- `autodataflow_analysis_total` - Analysis run count

### Logging

Logs are written to:
- stdout (structured format with request_id)
- `backend/data/logs/autodataflow.log` (file rotation: 100MB, 7 days retention)

## Scaling

- Increase `GUNICORN_WORKERS` for CPU-bound workloads (recommended: 2 * CPU cores + 1)
- Increase `GUNICORN_THREADS` for I/O-bound workloads
- Use a reverse proxy (nginx) in front of Gunicorn for SSL termination and load balancing

## Backup

Important data to back up:
- `backend/data/warehouse.db` - SQLite database
- `backend/data/*.json` - Configuration and report data
- `backend/config/*.yaml` - Quality rules configuration
