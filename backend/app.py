#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AutoDataFlow v3.0 — 工业级数据健康度治理 API 服务
================================================

工业级特性：
  - loguru 结构化日志，request_id 贯穿每个请求
  - Prometheus metrics：/metrics（请求计数、延迟直方图、活跃请求 gauge）
  - 滑动窗口限流：全局 + 单 IP 两层
  - SecurityHeadersMiddleware（HSTS/X-Content-Type/X-Frame/CSP）
  - 统一错误格式 {code, message, request_id}
  - CORS 白名单域名收紧
  - gunicorn 多 worker 部署（参见 gunicorn_conf.py）
  - 可配置数据质量规则引擎（YAML 声明式）
  - ETL 多目标数据清洗（Polars 驱动）
  - 行业合规规则库（GDPR / 个人信息保护法 / 数据安全法）
  - HTML / PDF 报告导出

Run:
  python app.py                    # 开发模式（uvicorn 单进程）
  gunicorn app:app -c gunicorn_conf.py  # 生产模式
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import subprocess
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Dict, List, Optional, Callable

# ---- 第三方依赖（部分可选，缺失时 graceful fallback）----
try:
    from fastapi import FastAPI, HTTPException, Header, Request, Response
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, JSONResponse
    from pydantic import BaseModel
except ImportError as e:
    raise RuntimeError(f"Missing fastapi/pydantic: {e}")

try:
    from loguru import logger
    import sys
    _LOGURU_CONFIGURED = True
except ImportError:
    _LOGURU_CONFIGURED = False
    import logging
    logger = logging.getLogger("autodataflow")

try:
    from prometheus_client import (
        Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
    )
    _PROMETHEUS_AVAILABLE = True
except ImportError:
    _PROMETHEUS_AVAILABLE = False
    Counter = Histogram = Gauge = None

try:
    import tenacity
    from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
    _TENACITY_AVAILABLE = True
except ImportError:
    _TENACITY_AVAILABLE = False

# ---- 本地模块 ----
from schema_change_detector import SchemaSnapshot
from causal_engine import CausalSchemaEngine

# v3.0 新增模块（webhook_alert 独立可调用）
try:
    from webhook_alert import WebhookAlert, run_alert_check
except ImportError:
    WebhookAlert = None
    run_alert_check = None

# v3.0 新增模块
try:
    from rule_engine import DataQualityRuleEngine
    from etl_cleaner import ETLCleaner, CleaningStrategy
    from compliance_engine import ComplianceEngine
    from report_export import ReportExporter
    # 独立规则引擎（rules.yaml 驱动）
    from rules_engine import RulesEngine
    # ETL Agent（CSV/JSON 输入清洗）
    from etl_agent import ETLCleanAgent
    # 合规规则库（预置 GDPR / 数据类型 / 新鲜度规则）
    from compliance_library import ComplianceLibrary
    _EXTENSIONS_AVAILABLE = True
except ImportError as e:
    _EXTENSIONS_AVAILABLE = False
    _EXTENSION_ERROR = str(e)

# ============================================================
# 0. 全局配置
# ============================================================

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# 因果推理引擎（v3.0，在lifespan中初始化）
_causal_engine = None

MAX_REPORT_FILES = 10
API_KEY = os.environ.get("AUTODATAFLOW_API_KEY", "dev-key-change-me")

# 允许的 CORS 白名单（具体域名）
ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
]

# ---- Prometheus Metrics ----
if _PROMETHEUS_AVAILABLE:
    REQUEST_COUNT = Counter(
        "autodataflow_http_requests_total",
        "Total HTTP requests",
        ["method", "endpoint", "status_code"]
    )
    REQUEST_LATENCY = Histogram(
        "autodataflow_http_request_duration_seconds",
        "HTTP request latency",
        ["method", "endpoint"],
        buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
    )
    ACTIVE_REQUESTS = Gauge(
        "autodataflow_active_requests",
        "Number of active requests"
    )
    ANALYSIS_COUNT = Counter(
        "autodataflow_analysis_total",
        "Total analysis runs",
        ["status"]
    )
else:
    class _NoOpCounter:
        def labels(self, **kwargs): return self
        def inc(self, n=1): pass
    class _NoOpHistogram:
        def labels(self, **kwargs): return self
        def observe(self, n): pass
    class _NoOpGauge:
        def labels(self, **kwargs): return self
        def set(self, n): pass
        def inc(self): pass
        def dec(self): pass
    REQUEST_COUNT = _NoOpCounter()
    REQUEST_LATENCY = _NoOpHistogram()
    ACTIVE_REQUESTS = _NoOpGauge()
    ANALYSIS_COUNT = _NoOpCounter()

# ============================================================
# 1. loguru 配置（结构化日志）
# ============================================================

def _configure_logging():
    if not _LOGURU_CONFIGURED:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        return

    logger.remove()

    logger.add(
        sys.stdout,
        level="INFO",
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan> | "
            "{message}"
        ),
        enqueue=True,
    )

    log_file = DATA_DIR / "logs" / "autodataflow.log"
    log_file.parent.mkdir(exist_ok=True)
    logger.add(
        str(log_file),
        level="DEBUG",
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
            "{level: <8} | "
            "{name}:{function}:{line} | "
            "{message}"
        ),
        rotation="100 MB",
        retention="7 days",
        compression="zip",
        enqueue=True,
    )

    logger.info("loguru configured", extra={"version": "3.0.0"})

_configure_logging()


# ============================================================
# 2. SQLite 连接池（工业级）
# ============================================================

_db_pool_lock = Lock()
_db_pool: List[sqlite3.Connection] = []
_DB_POOL_SIZE = 5
_DB_MAX_OVERFLOW = 20


def _get_db_pool_conn() -> sqlite3.Connection:
    with _db_pool_lock:
        if _db_pool:
            return _db_pool.pop()
    db_path = str(DATA_DIR / "warehouse.db")
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _return_db_pool_conn(conn: sqlite3.Connection):
    with _db_pool_lock:
        if len(_db_pool) < _DB_POOL_SIZE + _DB_MAX_OVERFLOW:
            _db_pool.append(conn)
        else:
            conn.close()


# ============================================================
# 3. 滑动窗口限流
# ============================================================

class SlidingWindowRateLimiter:
    def __init__(self, window_sec: int = 60, max_requests: int = 100):
        self.window_sec = window_sec
        self.max_requests = max_requests
        self._lock = Lock()
        self._requests: Dict[str, List[float]] = defaultdict(list)

    def _cleanup(self, key: str, now: float):
        cutoff = now - self.window_sec
        self._requests[key] = [t for t in self._requests[key] if t > cutoff]

    def is_allowed(self, key: str) -> bool:
        with self._lock:
            now = time.perf_counter()
            self._cleanup(key, now)
            if len(self._requests[key]) >= self.max_requests:
                return False
            self._requests[key].append(now)
            return True

    def remaining(self, key: str) -> int:
        with self._lock:
            now = time.perf_counter()
            self._cleanup(key, now)
            return max(0, self.max_requests - len(self._requests[key]))

    def reset(self, key: str):
        with self._lock:
            self._requests.pop(key, None)


_global_limiter = SlidingWindowRateLimiter(window_sec=60, max_requests=200)
_ip_limiter = SlidingWindowRateLimiter(window_sec=60, max_requests=60)


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip", "")
    if real_ip:
        return real_ip.strip()
    if request.client:
        return request.client.host or "unknown"
    return "unknown"


def _check_rate_limit(request: Request) -> tuple[bool, dict]:
    ip = _get_client_ip(request)
    ip_allowed = _ip_limiter.is_allowed(ip)
    ip_remaining = _ip_limiter.remaining(ip)
    global_allowed = _global_limiter.is_allowed("__global__")
    global_remaining = _global_limiter.remaining("__global__")

    info = {
        "ip": ip,
        "ip_remaining": ip_remaining,
        "global_remaining": global_remaining,
        "window_sec": _ip_limiter.window_sec,
    }
    if not ip_allowed:
        info["reason"] = "ip_rate_limit"
        return False, info
    if not global_allowed:
        info["reason"] = "global_rate_limit"
        return False, info
    return True, info


# ============================================================
# 4. 安全 Headers Middleware
# ============================================================

class SecurityHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        status_code = [200]

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_code[0] = message["status"]
                headers = list(message.get("headers", []))
                for name, value in self._security_headers().items():
                    headers.append((name.encode(), value.encode()))
                await send({
                    "type": "http.response.start",
                    "status": message["status"],
                    "headers": headers,
                })
            elif message["type"] == "http.response.body":
                await send(message)

        await self.app(scope, receive, send_wrapper)

    @staticmethod
    def _security_headers() -> Dict[str, str]:
        return {
            "Strict-Transport-Security": "max-age=31536000; includeSubDomains; preload",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "X-XSS-Protection": "1; mode=block",
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Content-Security-Policy": (
                "default-src 'self'; "
                "script-src 'self'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; "
                "connect-src 'self'; "
                "frame-ancestors 'none';"
            ),
            "Permissions-Policy": "accelerometer=(), camera=(), geolocation=(), microphone=()",
        }


# ============================================================
# 5. 请求 ID 注入 & 统一错误格式
# ============================================================

REQUEST_ID_HEADER = "X-Request-ID"

try:
    import contextvars
    _request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
    _request_context: contextvars.ContextVar[dict] = contextvars.ContextVar("request_context", default={})
except Exception:
    _request_id_var = None
    _request_context = {}


def get_request_id() -> str:
    if _request_id_var:
        return _request_id_var.get() or ""
    return _request_context.get("request_id", "")


def get_logger():
    rid = get_request_id()
    if _LOGURU_CONFIGURED:
        return logger.bind(request_id=rid) if rid else logger
    else:
        return logging.getLogger("autodataflow")


class AppException(HTTPException):
    def __init__(self, status_code: int, message: str, code: int = None):
        self.app_code = code or status_code
        super().__init__(status_code=status_code, detail=message)


def _build_error_response(status_code: int, message: str, request_id: str = "") -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "code": status_code,
            "message": message,
            "request_id": request_id,
        },
    )


# ============================================================
# 6. Prometheus 中间件
# ============================================================

async def _prometheus_middleware(request: Request, call_next: Callable):
    if not _PROMETHEUS_AVAILABLE:
        return await call_next(request)

    ACTIVE_REQUESTS.inc()
    method = request.method
    path_template = request.url.path
    for match in re.finditer(r'/[^/]+', path_template):
        segment = match.group()
        if segment[1:].replace('.', '').isalpha():
            continue
        path_template = path_template.replace(segment, '/{id}', 1)

    status_code = 500
    start_time = time.perf_counter()

    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    except Exception as exc:
        status_code = 500
        raise exc
    finally:
        duration = time.perf_counter() - start_time
        ACTIVE_REQUESTS.dec()
        REQUEST_COUNT.labels(method=method, endpoint=path_template, status_code=status_code).inc()
        REQUEST_LATENCY.labels(method=method, endpoint=path_template).observe(duration)


# ============================================================
# 7. Lifespan
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    get_logger().info("AutoDataFlow v3.0 starting up")

    for _ in range(_DB_POOL_SIZE):
        conn = _get_db_pool_conn()
        _return_db_pool_conn(conn)
    get_logger().info(f"SQLite connection pool initialized ({_DB_POOL_SIZE} connections)")

    _cleanup_old_reports()
    get_logger().info(f"Old reports cleaned (keep latest {MAX_REPORT_FILES})")

    try:
        db_path = str(DATA_DIR / "warehouse.db")
        if Path(db_path).exists():
            _causal_engine = CausalSchemaEngine(db_path)
            _causal_engine.build_causal_graph()
            get_logger().info("CausalSchemaEngine initialized (v3.0)")
        else:
            _causal_engine = None
            get_logger().warning("warehouse.db not found, causal engine not initialized")
    except Exception as e:
        _causal_engine = None
        get_logger().warning(f"CausalSchemaEngine init failed: {e}")

    yield

    with _db_pool_lock:
        for conn in _db_pool:
            try:
                conn.close()
            except Exception:
                pass
        _db_pool.clear()
    get_logger().info("Connection pool closed")


# ============================================================
# 8. FastAPI App
# ============================================================

app = FastAPI(
    title="AutoDataFlow v3.0 — Causal-Inference-Driven Schema Evolution",
    version="3.0.0",
    description=(
        "因果推理驱动的Schema演变预测框架 — Do-Calculus / 反事实推理 / "
        "可配置规则引擎 / ETL多目标清洗 / 行业合规规则库"
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["*"],
)
app.middleware("http")(_prometheus_middleware)


# ============================================================
# 9. 辅助函数
# ============================================================

def _cleanup_old_reports():
    reports = sorted(
        DATA_DIR.glob("quality_report_*.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in reports[MAX_REPORT_FILES:]:
        old.unlink(missing_ok=True)
    # Also clean HTML reports
    html_reports = sorted(
        DATA_DIR.glob("reports/quality_report_*.html"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in html_reports[MAX_REPORT_FILES:]:
        old.unlink(missing_ok=True)


def _load_json(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _get_db_tables() -> List[str]:
    db_path = DATA_DIR / "warehouse.db"
    if not db_path.exists():
        return []
    conn = _get_db_pool_conn()
    try:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        tables = [r[0] for r in cursor.fetchall()]
        return tables
    finally:
        _return_db_pool_conn(conn)


# ============================================================
# 10. 数据模型
# ============================================================

class TriggerAnalysisRequest(BaseModel):
    pass


# ============================================================
# 11. 核心端点
# ============================================================

@app.get("/health")
def health(request: Request):
    return {
        "status": "ok",
        "version": "3.0.0",
        "request_id": get_request_id(),
    }


@app.get("/metrics")
def metrics():
    if not _PROMETHEUS_AVAILABLE:
        return Response(content="# Prometheus client not installed", media_type="text/plain")
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/summary")
def get_summary(request: Request):
    health_data = _load_json(DATA_DIR / "health_report.json")
    trend_data = _load_json(DATA_DIR / "quality_trend.json")
    pred_data = _load_json(DATA_DIR / "predictive_analytics.json")
    ds_data = _load_json(DATA_DIR / "data_sources.json")
    history = trend_data.get("history", [])
    latest_trend = history[-1] if history else {}
    return {
        "version": "3.0.0",
        "request_id": get_request_id(),
        "total_tables": health_data.get("total_tables", 0),
        "total_rows": health_data.get("total_rows", 0),
        "avg_quality_score": health_data.get("avg_quality_score", 0),
        "latest_trend": latest_trend,
        "pipeline_health": pred_data.get("pipeline_health", "unknown"),
        "anomaly_alerts_count": len(pred_data.get("anomaly_alerts", [])),
        "recommendations": pred_data.get("recommendations", []),
        "data_sources": {
            "total": ds_data.get("total_sources", 0),
            "connected": ds_data.get("connected", 0),
            "warnings": ds_data.get("warnings", 0),
            "sources": [
                {
                    "id": s["id"],
                    "name": s["name"],
                    "type": s["type"],
                    "status": s["status"],
                    "latency_ms": s.get("latency_ms"),
                    "last_sync": s.get("last_sync"),
                    "quality_score": s.get("quality_score"),
                }
                for s in ds_data.get("sources", [])
            ],
        },
    }


@app.get("/tables")
def list_tables(request: Request):
    health_data = _load_json(DATA_DIR / "health_report.json")
    tables = health_data.get("tables", [])
    return {
        "request_id": get_request_id(),
        "total": len(tables),
        "tables": [
            {
                "name": t["name"],
                "schema": t.get("schema", "unknown"),
                "row_count": t["row_count"],
                "quality_score": t["quality_score"],
                "issues": t.get("issues", []),
            }
            for t in tables
        ],
    }


@app.get("/tables/{table_name}")
def get_table_detail(table_name: str, request: Request):
    health_data = _load_json(DATA_DIR / "health_report.json")
    for t in health_data.get("tables", []):
        if t["name"] == table_name:
            return {"request_id": get_request_id(), **t}
    raise HTTPException(404, f"Table {table_name} not found")


@app.get("/quality/trend")
def get_quality_trend(request: Request):
    trend_data = _load_json(DATA_DIR / "quality_trend.json")
    history = trend_data.get("history", [])
    return {"request_id": get_request_id(), "history": history, "count": len(history)}


@app.get("/lineage")
def get_lineage(request: Request):
    lineage_data = _load_json(DATA_DIR / "lineage.json")
    if isinstance(lineage_data, list):
        return {"request_id": get_request_id(), "lineage": lineage_data, "total_entries": len(lineage_data)}
    return {"request_id": get_request_id(), **lineage_data}


@app.get("/schema/changes")
def get_schema_changes(request: Request):
    db_path = str(DATA_DIR / "warehouse.db")
    detector = SchemaSnapshot(db_path)
    return detector.get_change_summary()


@app.get("/schema/changes/timeline")
def get_schema_timeline(request: Request):
    db_path = str(DATA_DIR / "warehouse.db")
    detector = SchemaSnapshot(db_path)
    return {"request_id": get_request_id(), "timeline": detector.get_timeline()}


@app.post("/schema/detect")
def detect_schema_changes(request: Request):
    db_path = str(DATA_DIR / "warehouse.db")
    detector = SchemaSnapshot(db_path)
    changes = detector.detect_changes()
    return {
        "request_id": get_request_id(),
        "detected": len(changes),
        "has_breaking": any(c.severity == "breaking" for c in changes),
        "changes": [c.to_dict() for c in changes],
    }


@app.get("/predictive")
def get_predictive(request: Request):
    return {"request_id": get_request_id(), **_load_json(DATA_DIR / "predictive_analytics.json")}


# =============================================================================
# 因果推理 API（v3.0）
# =============================================================================

@app.get("/causal/graph")
def get_causal_graph(request: Request):
    if _causal_engine is None:
        raise HTTPException(status_code=503, detail="Causal engine not initialized")
    graph = _causal_engine.get_causal_graph()
    return {"request_id": get_request_id(), "engine": "CausalSchemaEngine v3.0", **graph}


@app.post("/causal/predict")
def predict_schema_impact(
    op_type: str,
    table: str,
    column: Optional[str] = None,
    new_name: Optional[str] = None,
    new_dtype: Optional[str] = None,
    request: Request = None,
):
    if _causal_engine is None:
        raise HTTPException(status_code=503, detail="Causal engine not initialized")
    impacts = _causal_engine.predict_impact(op_type, table, column, new_name, new_dtype)
    return {
        "request_id": get_request_id(),
        "operation": f"do({op_type}({table}.{column or '?'}))",
        "engine": "Do-Calculus v3.0",
        "total_affected": len(impacts),
        "effects": [
            {
                "node": e.affected_node,
                "probability": round(e.probability, 3),
                "severity": e.severity,
                "type": e.effect_type,
                "explanation": e.explanation,
                "etl_jobs": e.affected_etl_jobs,
                "path": e.path,
            }
            for e in impacts
        ],
    }


@app.post("/causal/risk")
def explain_change_risk(
    op_type: str,
    table: str,
    column: Optional[str] = None,
    new_name: Optional[str] = None,
    new_dtype: Optional[str] = None,
    request: Request = None,
):
    if _causal_engine is None:
        raise HTTPException(status_code=503, detail="Causal engine not initialized")
    report = _causal_engine.explain_change_risk(op_type, table, column, new_name, new_dtype)
    return {"request_id": get_request_id(), "engine": "CausalSchemaEngine v3.0", **report}


@app.post("/causal/counterfactual")
def counterfactual_query(change: Dict, outcome: str = "ETL_Job", request: Request = None):
    if _causal_engine is None:
        raise HTTPException(status_code=503, detail="Causal engine not initialized")
    result = _causal_engine.counterfactual(change, outcome)
    return {
        "request_id": get_request_id(),
        "engine": "CounterfactualReasoner v3.0",
        "question": result.question,
        "factual": result.factual,
        "counterfactual": result.counterfactual,
        "difference": result.difference,
        "confidence": result.confidence,
        "causal_path": result.causal_path,
        "mitigation_suggestions": result.mitigation_suggestions,
    }


@app.post("/run/analysis")
def trigger_analysis(x_api_key: Optional[str] = Header(None), request: Request = None):
    log = get_logger()
    rid = get_request_id()
    log.bind(request_id=rid).info("Analysis triggered")

    if x_api_key is None or x_api_key != API_KEY:
        log.warning("Unauthorized analysis attempt")
        return _build_error_response(401, "Unauthorized: invalid or missing X-Api-Key header", rid)

    try:
        result = asyncio.run(_run_analysis_sync())
        try:
            db_path = str(DATA_DIR / "warehouse.db")
            detector = SchemaSnapshot(db_path)
            schema_changes = detector.detect_changes()
            schema_change_count = len(schema_changes)
        except Exception as e:
            log.error(f"Schema detection failed: {e}")
            schema_change_count = -1

        _cleanup_old_reports()
        ANALYSIS_COUNT.labels(status="success").inc()
        log.bind(request_id=rid, returncode=result.returncode).info("Analysis completed")

        return {
            "request_id": rid,
            "status": "completed",
            "returncode": result.returncode,
            "schema_changes_detected": schema_change_count,
            "stdout": result.stdout[-2000:] if result.stdout else "",
            "stderr": result.stderr[-500:] if result.stderr else "",
        }

    except subprocess.TimeoutExpired:
        ANALYSIS_COUNT.labels(status="timeout").inc()
        log.error("Analysis timed out (>300s)")
        return _build_error_response(500, "Analysis timed out (>300s)", rid)
    except Exception as e:
        ANALYSIS_COUNT.labels(status="error").inc()
        log.exception(f"Analysis failed: {e}")
        return _build_error_response(500, f"Analysis failed: {str(e)}", rid)


@app.get("/report/markdown")
def get_markdown_report(request: Request):
    reports = sorted(DATA_DIR.glob("quality_report_*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not reports:
        raise HTTPException(404, "No report found. Run /run/analysis first.")
    return FileResponse(reports[0], media_type="text/markdown")


@app.get("/report/latest")
def get_latest_report_path(request: Request):
    reports = sorted(DATA_DIR.glob("quality_report_*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not reports:
        return {"path": None, "message": "No report found"}
    return {
        "request_id": get_request_id(),
        "path": str(reports[0].name),
        "full_path": str(reports[0]),
    }


# =============================================================================
# 可配置规则引擎 API（v3.0 新增）
# =============================================================================

def _get_rule_engine():
    if not _EXTENSIONS_AVAILABLE:
        raise HTTPException(status_code=503, detail=f"Extension not available: {_EXTENSION_ERROR}")
    db_path = str(DATA_DIR / "warehouse.db")
    config_dir = Path(__file__).parent / "config"
    return DataQualityRuleEngine(db_path, config_dir)


@app.get("/rules/validate/all")
def validate_all_rules(request: Request):
    engine = _get_rule_engine()
    result = engine.validate_all()
    return {"request_id": get_request_id(), **result.to_dict()}


@app.get("/rules/validate/{table_name}")
def validate_table_rules(table_name: str, request: Request):
    engine = _get_rule_engine()
    result = engine.validate_table(table_name)
    return {"request_id": get_request_id(), **result.to_dict()}


@app.get("/rules/list")
def list_rules(request: Request):
    engine = _get_rule_engine()
    return {"request_id": get_request_id(), **engine.get_custom_rules()}


class AddRuleRequest(BaseModel):
    table: str
    rule: Dict


@app.post("/rules/add")
def add_custom_rule(body: AddRuleRequest, request: Request):
    engine = _get_rule_engine()
    result = engine.add_custom_rule(body.table, body.rule)
    return {"request_id": get_request_id(), **result}


@app.delete("/rules/{rule_id}")
def delete_custom_rule(rule_id: str, request: Request):
    engine = _get_rule_engine()
    result = engine.remove_custom_rule(rule_id)
    return {"request_id": get_request_id(), **result}


# =============================================================================
# ETL 多目标数据清洗 API（v3.0 新增）
# =============================================================================

class ETLCleanTarget(BaseModel):
    name: str
    rules: List[str]
    quality_threshold: Optional[float] = None


class ETLCleanRequest(BaseModel):
    source_table: str
    targets: List[ETLCleanTarget]


@app.post("/etl/clean")
def etl_clean(body: ETLCleanRequest, request: Request):
    if not _EXTENSIONS_AVAILABLE:
        raise HTTPException(status_code=503, detail="Extension not available")
    db_path = str(DATA_DIR / "warehouse.db")
    config_dir = Path(__file__).parent / "config"
    cleaner = ETLCleaner(db_path, config_dir)
    result = cleaner.clean(
        source_table=body.source_table,
        targets=[{"name": t.name, "rules": t.rules, "quality_threshold": t.quality_threshold}
                 for t in body.targets],
    )
    return {"request_id": get_request_id(), **result.to_dict()}


@app.post("/etl/clean/single")
def etl_clean_single(
    source_table: str,
    output_table: str,
    strategies: str,
    request: Request = None,
):
    if not _EXTENSIONS_AVAILABLE:
        raise HTTPException(status_code=503, detail="Extension not available")
    db_path = str(DATA_DIR / "warehouse.db")
    config_dir = Path(__file__).parent / "config"
    cleaner = ETLCleaner(db_path, config_dir)
    strategy_list = [s.strip() for s in strategies.split(",") if s.strip()]
    result = cleaner.clean_single(source_table, output_table, strategy_list)
    return {"request_id": get_request_id(), **result.to_dict()}


@app.get("/etl/strategies")
def etl_list_strategies(request: Request):
    return {"request_id": get_request_id(), "strategies": CleaningStrategy.STRATEGIES}


# =============================================================================
# 合规规则库 API（v3.0 新增）
# =============================================================================

@app.get("/compliance/check/{standard}")
def compliance_check(standard: str, request: Request):
    if not _EXTENSIONS_AVAILABLE:
        raise HTTPException(status_code=503, detail="Extension not available")
    db_path = str(DATA_DIR / "warehouse.db")
    config_dir = Path(__file__).parent / "config"
    engine = ComplianceEngine(db_path, config_dir)
    report = engine.run_compliance_check(standard)
    return {"request_id": get_request_id(), **report.to_dict()}


@app.get("/compliance/summary")
def compliance_summary(request: Request):
    if not _EXTENSIONS_AVAILABLE:
        raise HTTPException(status_code=503, detail="Extension not available")
    db_path = str(DATA_DIR / "warehouse.db")
    config_dir = Path(__file__).parent / "config"
    engine = ComplianceEngine(db_path, config_dir)
    return {"request_id": get_request_id(), **engine.get_compliance_summary()}


# =============================================================================
# 可配置规则引擎 API — rules.yaml 驱动（v3.0 新增）
# =============================================================================

@app.get("/api/rules")
def api_list_rules(request: Request):
    """列出当前加载的所有规则（来自 rules.yaml）"""
    if not _EXTENSIONS_AVAILABLE:
        raise HTTPException(status_code=503, detail="Extension not available")
    engine = RulesEngine(str(DATA_DIR / "warehouse.db"), Path(__file__).parent)
    rules = engine.get_rules()
    return {"request_id": get_request_id(), "rules": rules}


class ApiAddRuleRequest(BaseModel):
    table: str
    rule: Dict


@app.post("/api/rules")
def api_add_rule(body: ApiAddRuleRequest, request: Request):
    """动态添加一条规则（仅内存）"""
    if not _EXTENSIONS_AVAILABLE:
        raise HTTPException(status_code=503, detail="Extension not available")
    engine = RulesEngine(str(DATA_DIR / "warehouse.db"), Path(__file__).parent)
    result = engine.add_rule(body.table, body.rule)
    return {"request_id": get_request_id(), **result}


@app.delete("/api/rules/{rule_id}")
def api_delete_rule(rule_id: str, request: Request):
    """删除指定 ID 的规则（仅内存）"""
    if not _EXTENSIONS_AVAILABLE:
        raise HTTPException(status_code=503, detail="Extension not available")
    engine = RulesEngine(str(DATA_DIR / "warehouse.db"), Path(__file__).parent)
    result = engine.remove_rule(rule_id)
    return {"request_id": get_request_id(), **result}


class ApiCheckDataRequest(BaseModel):
    data: List[Dict]  # 输入数据（dict list）
    rules: Optional[List[Dict]] = None  # 可选规则覆盖


@app.post("/api/rules/check")
def api_check_data(body: ApiCheckDataRequest, request: Request):
    """检查输入数据（CSV/JSON）是否符合 rules.yaml 中的规则，返回违规列表 + 质量评分"""
    if not _EXTENSIONS_AVAILABLE:
        raise HTTPException(status_code=503, detail="Extension not available")
    engine = RulesEngine(str(DATA_DIR / "warehouse.db"), Path(__file__).parent)
    result = engine.check_data(body.data, body.rules)
    return {"request_id": get_request_id(), **result}


# =============================================================================
# ETL 增强 API — CSV/JSON 输入清洗（v3.0 新增）
# =============================================================================

class ETLCleanInputRequest(BaseModel):
    data: Union[str, List[Dict]]  # JSON 字符串 或 dict list
    format: Optional[str] = "json"  # 输出格式：json | csv | records
    apply_fixes: Optional[bool] = False
    rules_override: Optional[List[Dict]] = None


@app.post("/api/etl/clean")
def api_etl_clean(body: ETLCleanInputRequest, request: Request):
    """
    ETL 清洗 API：接受 CSV/JSON 输入，应用 rules.yaml 中的规则，
    返回清洗后的数据 + 质量评分。

    质量评分 = 100 - (violations / total_rows * 100)
    """
    if not _EXTENSIONS_AVAILABLE:
        raise HTTPException(status_code=503, detail="Extension not available")
    agent = ETLCleanAgent(str(DATA_DIR / "warehouse.db"), Path(__file__).parent)
    result = agent.process(
        data=body.data,
        rules_override=body.rules_override,
        apply_fixes=body.apply_fixes or False,
        output_format=body.format or "json",
    )
    return {"request_id": get_request_id(), **result}


# =============================================================================
# 合规规则库 API — 预置 GDPR / 数据类型 / 新鲜度规则（v3.0 新增）
# =============================================================================

@app.get("/api/compliance/rules")
def api_list_compliance_rules(standard: Optional[str] = None, request: Request = None):
    """列出所有可用的合规规则（GDPR / 数据类型校验 / 新鲜度）"""
    if not _EXTENSIONS_AVAILABLE:
        raise HTTPException(status_code=503, detail="Extension not available")
    lib = ComplianceLibrary(str(DATA_DIR / "warehouse.db"), Path(__file__).parent)
    rules = lib.list_rules(standard=standard)
    return {"request_id": get_request_id(), "total": len(rules), "rules": rules}


class ComplianceApplyRequest(BaseModel):
    data: List[Dict]
    rule_ids: Optional[List[str]] = None
    dry_run: Optional[bool] = False


@app.post("/api/compliance/apply")
def api_compliance_apply(body: ComplianceApplyRequest, request: Request):
    """
    对数据集应用合规规则（脱敏 / 校验 / 新鲜度检查）。
    返回脱敏后数据 + 违规报告。
    """
    if not _EXTENSIONS_AVAILABLE:
        raise HTTPException(status_code=503, detail="Extension not available")
    lib = ComplianceLibrary(str(DATA_DIR / "warehouse.db"), Path(__file__).parent)
    result = lib.apply(data=body.data, rule_ids=body.rule_ids, dry_run=body.dry_run or False)
    return {"request_id": get_request_id(), **result}


# =============================================================================
# 报告导出 API — HTML / 纯文本（v3.0 新增）
# =============================================================================

class ReportExportRequest(BaseModel):
    format: Optional[str] = "html"  # html | text


@app.post("/api/reports/export")
def api_export_report(body: ReportExportRequest, request: Request):
    """
    生成并返回质量报告（HTML 或纯文本）。
    HTML 报告包含：规则违规表格、质量评分、数据摘要。
    纯文本报告可通过 ?format=text URL 参数获取（GET）。
    """
    if not _EXTENSIONS_AVAILABLE:
        raise HTTPException(status_code=503, detail="Extension not available")
    db_path = str(DATA_DIR / "warehouse.db")
    config_dir = Path(__file__).parent / "config"
    exporter = ReportExporter(db_path, config_dir)

    if body.format == "text":
        # 生成纯文本报告
        rule_engine = RulesEngine(db_path, Path(__file__).parent)
        result = rule_engine.validate_all()
        lines = [
            "=" * 60,
            "AutoDataFlow 数据质量报告",
            "=" * 60,
            f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"总体质量评分: {result.overall_score:.1f}/100",
            f"检查表数: {result.total_tables}",
            f"总规则数: {result.total_rules}",
            f"通过: {result.total_passed} | 失败: {result.total_failed}",
            "",
            "--- 规则违规详情 ---",
        ]
        for tr in result.table_results:
            if tr.failed > 0:
                lines.append(f"\n表: {tr.table} (质量分: {tr.quality_score:.1f})")
                for v in tr.violations:
                    lines.append(f"  [{v.severity}] {v.rule_id} | {v.field}: {v.message}")
        lines.append("\n" + "=" * 60)
        return {
            "request_id": get_request_id(),
            "format": "text",
            "report": "\n".join(lines),
        }
    else:
        # 生成 HTML 报告
        html_path = exporter.generate_html_report()
        return {"request_id": get_request_id(), "format": "html", "path": str(html_path), "url": f"/report/export/html"}


@app.get("/api/reports/export")
def api_export_report_get(request: Request, format: str = "html"):
    """GET 版本：?format=text 返回纯文本报告"""
    if not _EXTENSIONS_AVAILABLE:
        raise HTTPException(status_code=503, detail="Extension not available")
    db_path = str(DATA_DIR / "warehouse.db")
    rule_engine = RulesEngine(db_path, Path(__file__).parent)
    result = rule_engine.validate_all()

    if format == "text":
        lines = [
            "=" * 60,
            "AutoDataFlow 数据质量报告",
            "=" * 60,
            f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"总体质量评分: {result.overall_score:.1f}/100",
            f"检查表数: {result.total_tables} | 总规则数: {result.total_rules}",
            f"通过: {result.total_passed} | 失败: {result.total_failed}",
            "",
            "--- 规则违规详情 ---",
        ]
        for tr in result.table_results:
            if tr.failed > 0:
                lines.append(f"\n表: {tr.table} (质量分: {tr.quality_score:.1f})")
                for v in tr.violations:
                    lines.append(f"  [{v.severity}] {v.rule_id} | {v.field}: {v.message}")
        lines.append("\n" + "=" * 60)
        return {
            "request_id": get_request_id(),
            "format": "text",
            "report": "\n".join(lines),
        }

    # HTML redirect
    return JSONResponse({
        "request_id": get_request_id(),
        "message": "Use POST /api/reports/export with format=html, or GET /report/export/html",
        "html_url": "/report/export/html",
    })


# =============================================================================
# 报告导出 API（v3.0 新增）
# =============================================================================

@app.get("/report/export/html")
def export_html_report(request: Request):
    if not _EXTENSIONS_AVAILABLE:
        raise HTTPException(status_code=503, detail="Extension not available")
    db_path = str(DATA_DIR / "warehouse.db")
    config_dir = Path(__file__).parent / "config"
    exporter = ReportExporter(db_path, config_dir)
    html_path = exporter.generate_html_report()
    return FileResponse(html_path, media_type="text/html", filename=html_path.name)


@app.get("/report/export/pdf")
def export_pdf_report(request: Request):
    if not _EXTENSIONS_AVAILABLE:
        raise HTTPException(status_code=503, detail="Extension not available")
    db_path = str(DATA_DIR / "warehouse.db")
    config_dir = Path(__file__).parent / "config"
    exporter = ReportExporter(db_path, config_dir)
    result_path = exporter.generate_pdf_report()
    if result_path is None:
        raise HTTPException(status_code=503, detail="PDF generation failed: weasyprint not installed")
    if result_path.suffix == ".html":
        return JSONResponse({
            "request_id": get_request_id(),
            "fallback": "html",
            "message": "weasyprint 未安装，请在浏览器中打开 HTML 报告使用 Ctrl+P 打印为 PDF",
            "html_url": "/report/export/html",
        })
    return FileResponse(result_path, media_type="application/pdf", filename=result_path.name)


@app.get("/report/list")
def list_reports(request: Request):
    if not _EXTENSIONS_AVAILABLE:
        raise HTTPException(status_code=503, detail="Extension not available")
    db_path = str(DATA_DIR / "warehouse.db")
    config_dir = Path(__file__).parent / "config"
    exporter = ReportExporter(db_path, config_dir)
    return {"request_id": get_request_id(), **exporter.get_latest_reports()}


# =============================================================================
# 12. Webhook 告警 API（飞书/钉钉）
# =============================================================================

@app.post("/alerts/check")
def check_alerts(request: Request):
    """
    执行数据质量告警检查，并推送飞书/钉钉。
    """
    log = get_logger()
    rid = get_request_id()
    try:
        result = run_alert_check(str(DATA_DIR))
        log.bind(request_id=rid).info(f"Alert check done: anomalies={result.get('anomaly_count', 0)}")
        return {"request_id": rid, **result}
    except Exception as e:
        log.exception(f"Alert check failed: {e}")
        return _build_error_response(500, f"Alert check failed: {str(e)}", rid)


@app.get("/alerts/channels")
def get_alert_channels(request: Request):
    """查看当前告警渠道配置（不含密钥）"""
    alert = WebhookAlert(str(DATA_DIR))
    cfg = alert.config
    # 脱敏处理
    safe_cfg = {}
    for ch, val in cfg.items():
        if isinstance(val, dict):
            safe = {k: (v if k != "secret" and k != "webhook_url" else "***") for k, v in val.items()}
            safe_cfg[ch] = safe
        else:
            safe_cfg[ch] = val
    return {"request_id": get_request_id(), "config": safe_cfg}


class AlertChannelUpdateRequest(BaseModel):
    channel: str  # feishu | dingtalk
    enabled: bool
    webhook_url: Optional[str] = None
    mention_list: Optional[List[str]] = None
    secret: Optional[str] = None


@app.put("/alerts/channels/{channel}")
def update_alert_channel(channel: str, body: AlertChannelUpdateRequest, request: Request):
    """更新告警渠道配置"""
    alert = WebhookAlert(str(DATA_DIR))
    if channel not in alert.config:
        raise HTTPException(404, f"Unknown channel: {channel}")
    ch_cfg = alert.config[channel]
    ch_cfg["enabled"] = body.enabled
    if body.webhook_url is not None:
        ch_cfg["webhook_url"] = body.webhook_url
    if body.mention_list is not None:
        ch_cfg["mention_list"] = body.mention_list
    if body.secret is not None:
        ch_cfg["secret"] = body.secret
    alert.save_config()
    return {"request_id": get_request_id(), "channel": channel, "updated": True}


# =============================================================================
# 13. 全局异常处理
# =============================================================================

@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    return _build_error_response(exc.status_code, exc.detail, get_request_id())


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return _build_error_response(exc.status_code, exc.detail, get_request_id())


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    log = get_logger()
    log.exception(f"Unhandled exception: {exc}")
    return _build_error_response(500, "Internal server error", get_request_id())


# =============================================================================
# 13. 限流中间件
# =============================================================================

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next: Callable):
    allowed, info = _check_rate_limit(request)
    if not allowed:
        rid = request.headers.get(REQUEST_ID_HEADER, "") or str(uuid.uuid4())[:8]
        reason = info.get("reason", "rate_limit")
        log = get_logger()
        log.warning(f"Rate limit hit | ip={info['ip']} | reason={reason}")
        return JSONResponse(
            status_code=429,
            content={
                "code": 429,
                "message": f"Rate limit exceeded: {reason}. "
                           f"IP remaining: {info['ip_remaining']}, "
                           f"Global remaining: {info['global_remaining']}, "
                           f"window: {info['window_sec']}s",
                "request_id": rid,
            },
            headers={"X-Request-ID": rid, "Retry-After": str(_ip_limiter.window_sec)},
        )

    incoming_rid = request.headers.get(REQUEST_ID_HEADER, "")
    if not incoming_rid:
        incoming_rid = str(uuid.uuid4())[:16]

    if _request_id_var:
        _request_id_var.set(incoming_rid)
        _request_context.set({"request_id": incoming_rid})

    response = await call_next(request)
    response.headers[REQUEST_ID_HEADER] = incoming_rid
    return response


# =============================================================================
# 14. 子进程执行
# =============================================================================

def _run_analysis_sync():
    backend_path = Path(__file__).parent
    script_path = backend_path / "auto_data_flow.py"
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            subprocess.run,
            ["python", str(script_path)],
            capture_output=True,
            text=True,
            timeout=300,
            encoding="utf-8",
            errors="replace",
        )
        return future.result()


# =============================================================================
# 15. 启动
# =============================================================================

def main():
    import uvicorn
    logger.info(f"AutoDataFlow v3.0 API starting | data_dir={DATA_DIR} | docs=/docs | metrics=/metrics")
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info", access_log=True)


if __name__ == "__main__":
    main()