# AutoDataFlow API Documentation

## Base URL

```
http://localhost:8080
```

## Authentication

Protected endpoints require the `X-Api-Key` header:

```
X-Api-Key: <your-api-key>
```

Default key: `dev-key-change-me` (change via `AUTODATAFLOW_API_KEY` env var)

---

## Endpoints

### Health Check

```
GET /health
```

Response:
```json
{
  "status": "ok",
  "version": "3.0.0",
  "request_id": "abc123"
}
```

### Prometheus Metrics

```
GET /metrics
```

Returns Prometheus-format metrics including request counts, latency histograms, and active request gauges.

### Dashboard Summary

```
GET /summary
```

Returns aggregated dashboard data including table counts, quality scores, trend data, and recommendations.

### Tables

```
GET /tables
GET /tables/{table_name}
```

List all tables or get detailed information about a specific table.

### Causal Graph

```
GET /causal/graph
```

Returns the causal lineage graph with nodes, edges, and statistics.

### Impact Prediction

```
POST /causal/predict?op_type={op}&table={table}&column={column}
```

Predict the causal impact of a schema change operation using Do-Calculus.

Parameters:
- `op_type`: One of `drop_column`, `rename_column`, `dtype_change`, `add_column`, `truncate_table`
- `table`: Target table name
- `column`: Target column name (optional for table-level operations)
- `new_name`: New column name (for rename operations)
- `new_dtype`: New data type (for type change operations)

### Risk Assessment

```
POST /causal/risk?op_type={op}&table={table}&column={column}
```

Returns a comprehensive risk assessment report including risk level, affected nodes, critical ETL jobs, and recommendations.

### Counterfactual Reasoning

```
POST /causal/counterfactual
```

Request body:
```json
{
  "type": "rename_column",
  "table": "sales",
  "column": "amount",
  "new_name": "revenue"
}
```

Query parameter: `outcome=ETL_RevenueAggregation`

### ETL Cleaning

```
POST /etl/clean
```

Request body:
```json
{
  "source_table": "ods_sales",
  "targets": [
    {
      "name": "dwd_sales",
      "rules": ["drop_nulls", "outlier_clip"],
      "quality_threshold": 80.0
    }
  ]
}
```

### CSV/JSON Data Cleaning

```
POST /api/etl/clean
```

Request body:
```json
{
  "data": [
    {"id": 1, "name": "Alice", "amount": 100},
    {"id": 2, "name": null, "amount": -50}
  ],
  "format": "json",
  "apply_fixes": true
}
```

### Compliance Check

```
GET /compliance/check/{standard}
```

Standards: `gdpr`, `datatype_validation`, `freshness`, `referential_integrity`, `all`

### Apply Compliance Rules

```
POST /api/compliance/apply
```

Request body:
```json
{
  "data": [
    {"id": 1, "email": "alice@example.com", "phone": "13812345678"}
  ],
  "rule_ids": ["gdpr_email_mask"],
  "dry_run": false
}
```

### Report Export

```
POST /api/reports/export
GET  /api/reports/export?format=text
GET  /report/export/html
GET  /report/export/pdf
```

### Alerts

```
POST /alerts/check
GET  /alerts/channels
PUT  /alerts/channels/{channel}
```

### Trigger Analysis

```
POST /run/analysis
Header: X-Api-Key: <key>
```

---

## Error Format

All errors return:
```json
{
  "code": 401,
  "message": "Unauthorized: invalid or missing X-Api-Key header",
  "request_id": "abc123"
}
```

## Rate Limiting

- Global: 200 requests/minute
- Per IP: 60 requests/minute
- Exceeded: HTTP 429 with `Retry-After` header
