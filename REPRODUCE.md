# REPRODUCE.md - AutoDataFlow

## Prerequisites

- **Python**: 3.10+
- **OS**: Linux / macOS / Windows
- **GPU**: Not required

## Install

```bash
cd AutoDataFlow
pip install -r requirements.txt
```

Dependencies: fastapi, uvicorn, pydantic, pyyaml, polars, loguru, prometheus-client, tenacity, gunicorn, pytest

## Smoke Test

```bash
python -m pytest backend/tests/ -v
```

## Run Server

```bash
cd backend
python app.py
```

## Expected Outputs

- Causal lineage graph visualization
- Schema change impact prediction via Do-Calculus
- Counterfactual quality reasoning
- Pre-loaded data in `backend/data/`:
  - `data_sources.json`, `health_report.json`, `lineage.json`
  - `predictive_analytics.json`, `quality_trend.json`

## Known Issues

- No hardcoded paths detected
- Uses polars (Rust-based DataFrame library) - install may take time
- PDF export via weasyprint is optional
