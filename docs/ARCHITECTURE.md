# AutoDataFlow Architecture Guide

## System Overview

AutoDataFlow is a causal-driven data health governance platform that uses causal inference to predict the impact of schema changes on downstream ETL pipelines.

## Multi-Agent Architecture

### Schema Agent
- Scans input data directories (CSV/JSON/Parquet)
- Infers data types using Polars
- Generates DDL and creates SQLite tables
- Builds ODS -> DWD -> ADS three-layer data warehouse

### ETL Agent
- Polars-driven data cleaning with 15+ strategies
- Null handling: median/mean/zero fill, forward fill, drop, flag
- Outlier detection: IQR clipping, dropping, flagging, winsorization
- Deduplication, type casting, string trimming

### Observer Agent
- Performance monitoring: query time measurement
- Auto-indexing: creates indexes when queries exceed thresholds
- Schema change detection with DDL hash comparison

### Visualization Agent
- Quality report generation (Markdown, HTML, PDF)
- Trend analysis and predictive analytics
- Webhook alert dispatch (Feishu, DingTalk)

## Causal Inference Pipeline

```
Data Sources -> Schema Agent -> Causal Graph Builder -> Do-Calculus Engine
                                                        |
                                                        v
                                              Counterfactual Reasoner
                                                        |
                                                        v
                                              Impact Prediction Report
```

### Causal Graph Construction
1. Scan all tables and columns in the warehouse
2. Infer foreign key relationships via value overlap analysis
3. Detect ETL transformations via Pearson correlation
4. Identify quality propagation via null pattern correlation
5. Build directed graph with mechanism-labeled edges

### Do-Calculus Engine
Implements Pearl's three rules for causal inference:
- Rule 1: Addition/Removal of Evidence
- Rule 2: Action/Observation Exchange
- Rule 3: Action/Action Exchange

### Counterfactual Reasoner
Answers "what-if" questions by:
1. Constructing the DoOperation
2. Predicting intervention effects
3. Comparing factual vs counterfactual outcomes
4. Generating mitigation suggestions

## Data Flow

```
input_data/*.csv -> SchemaAgent.create_tables() -> SQLite warehouse.db
                                                         |
                                                    ETLAgent.profile_table()
                                                         |
                                                    ObserverAgent.run_observer_duty()
                                                         |
                                                    CausalSchemaEngine.build_causal_graph()
                                                         |
                                                    API Endpoints (/causal/predict, /causal/risk)
```

## Storage Layer

- **SQLite**: Warehouse tables, lineage data
- **JSON Files**: Health reports, quality trends, predictive analytics, schema snapshots
- **YAML**: Quality rules, compliance rules

## Security Architecture

- API key authentication for protected endpoints
- CORS whitelist (specific origins only)
- Security headers middleware (HSTS, CSP, X-Frame-Options)
- Sliding-window rate limiting (global + per-IP)
- Non-root Docker container
