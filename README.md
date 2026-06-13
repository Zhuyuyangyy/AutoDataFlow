# AutoDataFlow v3.0

**Causal-Driven Data Health Governance Platform | Automatic Data Flow / ETL**

[![CI](https://github.com/AutoDataFlow/AutoDataFlow/actions/workflows/ci.yml/badge.svg)](https://github.com/AutoDataFlow/AutoDataFlow/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/Docker-ready-blue.svg)](Dockerfile)

> AutoDataFlow constructs causal lineage graphs over data warehouse schemas, applies Do-Calculus intervention reasoning to forecast downstream ETL breakage, and performs counterfactual quality analysis before destructive schema changes are deployed.

---

## Table of Contents

- [Overview](#overview)
- [Key Features](#key-features)
- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Quick Start](#quick-start)
- [Docker Deployment](#docker-deployment)
- [Project Structure](#project-structure)
- [Core Modules](#core-modules)
- [API Reference](#api-reference)
- [Configuration](#configuration)
- [Testing](#testing)
- [Innovation & Patents](#innovation--patents)
- [Roadmap](#roadmap)
- [License](#license)

---

## Overview

Modern data warehouses evolve continuously: columns are renamed, data types are narrowed, tables are split or merged. Each such change propagates through downstream ETL jobs, dashboards, and analytical models. Existing tools detect data quality issues **after** they occur but cannot predict the impact of a schema change **before** it is applied.

AutoDataFlow addresses this gap by introducing **causal inference** into schema lineage analysis. Rather than treating lineage as a correlation graph, it models lineage as a **causal graph** where each edge carries a mechanism label and a strength/confidence score.

---

## Key Features

### Core Innovation

- **Causal Lineage Graph** -- Automatic extraction of causal edges from warehouse metadata, DDL, and ETL job definitions with five mechanism types (FOREIGN_KEY, ETL_TRANSFORM, SCHEMA_DEPENDENCY, QUALITY_PROPAGATION, CASCADING_FAILURE).
- **Do-Calculus Impact Prediction** -- Formal intervention reasoning (Pearl's do-operator) to answer "if we drop column X, which ETL jobs will break and with what probability?"
- **Counterfactual Quality Inference** -- What-if analysis comparing the observed data quality trajectory against the hypothetical trajectory under a proposed schema change.
- **Multi-Agent Architecture** -- Schema Agent, ETL Agent, Observer Agent, Visualization Agent coordinated through a shared causal graph.

### Industrial-Grade Platform

- **Configurable Rule Engine** -- YAML-driven data quality rules (null_check, regex_match, range_check, uniqueness, freshness, enum_check).
- **Multi-Target ETL Cleaning** -- Polars-driven data cleaning with 15+ strategies (IQR outlier clipping, null filling, deduplication, winsorization).
- **Industry Compliance** -- GDPR, PIPL, DSL compliance rules with field-level masking (email, phone, ID card, bank account).
- **Report Export** -- HTML/PDF quality reports with trend visualization.
- **Webhook Alerts** -- Feishu and DingTalk integration with retry and degradation.
- **Observability** -- Structured logging (loguru), Prometheus metrics, per-request tracing.
- **Security** -- Sliding-window rate limiting, CORS whitelist, security headers (HSTS, CSP, X-Frame-Options).
- **Production Deployment** -- Gunicorn multi-worker, Docker, health checks.

---

## Architecture

```
+---------------------------+
|   Vue3 + ECharts Dashboard|
+------------+--------------+
             |
             v
+------------------------------------------------------+
|                FastAPI Application                     |
+------------------------------------------------------+
|  +-------------+   do(change)  +-------------------+ |
|  | Schema Agent| ------------> | Causal Lineage    | |
|  +------+------+               | Graph Builder     | |
|         |                      +--------+----------+ |
|         v                               |            |
|  +-------------+               +--------v----------+ |
|  | ETL Agent   | <-------------| Do-Calculus Engine| |
|  +------+------+               +--------+----------+ |
|         |                               |            |
|         v                      +--------v----------+ |
|  +-------------+               | Counterfactual    | |
|  |Observer Agt.| <-------------| Reasoner          | |
|  +------+------+               +--------+----------+ |
|         |                               |            |
|         v                      +--------v----------+ |
|  +-------------+               | Viz Agent         | |
|  |  Alerts     | <-------------| Reports + Alerts  | |
|  +-------------+               +-------------------+ |
+------------------------------------------------------+
         |              |               |
    +----+----+   +-----+-----+  +------+------+
    | SQLite  |   | JSON Store|  | Webhook     |
    | Warehouse|   | Dashboard |  | (Feishu/Ding)|
    +---------+   +-----------+  +-------------+
```

---

## Tech Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| Backend | Python 3.10+, FastAPI | API service and agent orchestration |
| Causal Engine | Custom CausalSchemaEngine | Causal graph, Do-Calculus, counterfactual |
| Data Processing | Polars, PyYAML | ETL cleaning, rule configuration |
| Storage | SQLite, JSON | Persistence layer |
| Frontend | Vue 3, ECharts 5.4 | Interactive dashboard |
| Observability | loguru, Prometheus | Structured logging, metrics |
| Alerts | Feishu, DingTalk | Notification dispatch |
| Deployment | Gunicorn, Docker | Production deployment |

---

## Quick Start

### Prerequisites

- Python 3.10+
- pip

### Installation

```bash
git clone <repository-url>
cd AutoDataFlow
pip install -r requirements.txt
```

### Generate Sample Data & Run Analysis

```bash
cd backend
python auto_data_flow.py
```

### Start API Service

```bash
# Development mode
python app.py

# Production mode
gunicorn app:app -c gunicorn_conf.py
```

The service starts on `http://localhost:8080`. API docs at `/docs`.

### Run Tests

```bash
python -m pytest tests/ -v --cov=backend --cov-report=term-missing
```

---

## Docker Deployment

### Build & Run

```bash
# Build image
docker build -t autodataflow .

# Run container
docker run -d -p 8080:8080 --name autodataflow autodataflow
```

### Docker Compose (with Redis for rate limiting)

```bash
docker-compose up -d
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AUTODATAFLOW_API_KEY` | `dev-key-change-me` | API key for analysis trigger |
| `GUNICORN_WORKERS` | `4` | Number of worker processes |
| `GUNICORN_THREADS` | `2` | Threads per worker |
| `GUNICORN_TIMEOUT` | `60` | Request timeout (seconds) |
| `ADF_PORT` | `8080` | Service port |

---

## Project Structure

```
AutoDataFlow/
+-- backend/
|   +-- app.py                        # FastAPI application (v3.0)
|   +-- causal_engine.py              # Causal inference engine
|   +-- causal_mechanism_inferrer.py  # Statistical mechanism inference
|   +-- schema_change_detector.py     # Schema snapshot & change detection
|   +-- auto_data_flow.py             # Agent Swarm orchestrator
|   +-- etl_agent.py                  # ETL clean agent (CSV/JSON input)
|   +-- etl_cleaner.py                # Multi-target ETL cleaner (Polars)
|   +-- rule_engine.py                # YAML-driven quality rule engine
|   +-- rules_engine.py               # Independent rules engine
|   +-- compliance_engine.py          # Industry compliance engine
|   +-- compliance_library.py         # Compliance rule library (GDPR/PIPL/DSL)
|   +-- webhook_alert.py              # Feishu/DingTalk alert system
|   +-- report_export.py              # HTML/PDF report export
|   +-- generate_report.py            # Markdown report generator
|   +-- config_loader.py              # YAML configuration loader
|   +-- gunicorn_conf.py              # Gunicorn production config
|   +-- config/
|   |   +-- quality_rules.yaml        # Quality rules configuration
|   +-- data/                         # Runtime data (gitignored)
|   +-- requirements.txt
+-- frontend/
|   +-- index.html                    # Vue3 + ECharts dashboard
+-- tests/
|   +-- __init__.py
|   +-- test_smoke.py                 # Smoke tests
|   +-- test_causal_engine.py         # Causal engine tests
|   +-- test_etl_cleaner.py           # ETL cleaner tests
|   +-- test_rule_engine.py           # Rule engine tests
|   +-- test_compliance.py            # Compliance tests
|   +-- test_schema_detector.py       # Schema detector tests
|   +-- test_api_endpoints.py         # API endpoint tests
+-- docs/
|   +-- API.md                        # API documentation
|   +-- ARCHITECTURE.md               # Architecture guide
|   +-- DEPLOYMENT.md                 # Deployment guide
|   +-- INNOVATION.md                 # Innovation analysis
+-- input_data/
|   +-- products.csv                  # Sample product data
|   +-- sales.csv                     # Sample sales data
+-- docker-compose.yml
+-- Dockerfile
+-- requirements.txt
+-- .github/workflows/ci.yml
+-- TODO.md
+-- INNOVATION_ROADMAP.md
+-- OPTIMIZATION_REPORT.md
+-- README.md
```

---

## Core Modules

### CausalSchemaEngine (`causal_engine.py`)

Central module implementing causal graph construction and inference with five causal mechanisms:

| Mechanism | Description | Example |
|-----------|-------------|---------|
| `FOREIGN_KEY` | Deterministic constraint causality | `orders.customer_id` -> `customers.id` |
| `ETL_TRANSFORM` | Aggregation/transformation | `raw_sales.amount` -> `daily_summary.total_revenue` |
| `SCHEMA_DEPENDENCY` | Column reference dependency | `orders.status` -> `report.status` |
| `QUALITY_PROPAGATION` | Quality metric propagation | `products.stock` -> `delivery_time` |
| `CASCADING_FAILURE` | Failure propagation chain | `payments.status` -> `shipped_at` |

### ETL Multi-Target Cleaner (`etl_cleaner.py`)

15+ cleaning strategies: `drop_nulls`, `fill_null_median`, `fill_null_mean`, `fill_null_zero`, `flag_nulls`, `outlier_clip`, `outlier_drop`, `outlier_flag`, `outlier_winsorize`, `deduplicate`, `deduplicate_all`, `cast_datetime`, `cast_numeric`, `trim_strings`, `aggregate_sum`, `aggregate_avg`, `aggregate_count`.

### Compliance Engine (`compliance_engine.py`)

Four compliance standards with field-level data masking:

- **GDPR** -- Email, phone, ID card, bank account, address, name masking
- **PIPL/DSL** -- China personal information protection
- **PCI-DSS** -- Payment card data security
- **Data Type Validation** -- Strict type checking with format validation

---

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/metrics` | GET | Prometheus metrics |
| `/summary` | GET | Dashboard summary |
| `/tables` | GET | List all tables |
| `/causal/graph` | GET | Causal lineage graph |
| `/causal/predict` | POST | Do-Calculus impact prediction |
| `/causal/risk` | POST | Change risk assessment |
| `/causal/counterfactual` | POST | Counterfactual reasoning |
| `/rules/validate/all` | GET | Validate all quality rules |
| `/etl/clean` | POST | Multi-target ETL cleaning |
| `/api/etl/clean` | POST | CSV/JSON data cleaning |
| `/compliance/check/{standard}` | GET | Compliance check |
| `/api/compliance/apply` | POST | Apply compliance rules |
| `/api/reports/export` | POST | Generate HTML/text report |
| `/alerts/check` | POST | Run alert check |
| `/run/analysis` | POST | Trigger full analysis (requires API key) |

Full interactive documentation at `/docs` (Swagger UI) and `/redoc`.

---

## Configuration

### Quality Rules (`backend/config/quality_rules.yaml`)

```yaml
scoring_weights:
  null_rate: 0.4
  outlier_rate: 0.35
  uniqueness: 0.25

data_quality_rules:
  defaults:
    - id: "null_check_email"
      type: "null_check"
      field: "email"
      severity: "critical"
      enabled: true
  table_rules:
    "*":
      - id: "trim_all_strings"
        type: "regex_match"
        field: ".*"
        pattern: "^\\S.*\\S$"
        severity: "warning"
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AUTODATAFLOW_API_KEY` | `dev-key-change-me` | API key for protected endpoints |
| `AUTODATAFLOW_PORT` | `8080` | Service port |
| `GUNICORN_WORKERS` | `4` | Worker processes |
| `GUNICORN_THREADS` | `2` | Threads per worker |

---

## Testing

```bash
# Run all tests
python -m pytest tests/ -v

# Run with coverage
python -m pytest tests/ -v --cov=backend --cov-report=term-missing

# Run specific test file
python -m pytest tests/test_causal_engine.py -v

# Run smoke tests only
python -m pytest tests/test_smoke.py -v
```

---

## Innovation & Patents

See [INNOVATION_ROADMAP.md](INNOVATION_ROADMAP.md) for the full innovation roadmap including:

1. **Patent 1**: Causal Lineage Graph Construction via Statistical Mechanism Inference
2. **Patent 2**: Do-Calculus-Based Schema Change Impact Prediction Engine
3. **Patent 3**: Counterfactual Data Quality Reasoning Framework
4. **Patent 4**: Intelligent Data Mapping with Auto-Schema Discovery

---

## Roadmap

- [x] Causal lineage graph construction
- [x] Do-Calculus impact prediction
- [x] Counterfactual quality reasoning
- [x] Configurable rule engine
- [x] Multi-target ETL cleaning
- [x] Industry compliance library
- [x] HTML/PDF report export
- [x] Webhook alerts (Feishu/DingTalk)
- [x] Docker deployment
- [x] CI/CD pipeline
- [ ] Apache Spark / dbt integration
- [ ] Graph neural network causal strength estimation
- [ ] Streaming schema change events (Kafka, Flink CDC)
- [ ] Interactive what-if scenario exploration UI
- [ ] CI/CD pre-deployment schema impact checks

---

## License

MIT License. See [LICENSE](LICENSE) for details.
