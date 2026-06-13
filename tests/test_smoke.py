"""
AutoDataFlow v3.0 Smoke Tests
==============================
Basic import and sanity checks for all core modules.
Run: pytest tests/test_smoke.py -v
"""

import importlib
import sys
import os
import pytest

# Ensure backend is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


# ============================================================
# Module Import Tests
# ============================================================

class TestModuleImports:
    """Verify all core modules can be imported without errors."""

    def test_import_app(self):
        """FastAPI main app should import."""
        mod = importlib.import_module("app")
        assert hasattr(mod, "app"), "app.py should export 'app' FastAPI instance"

    def test_import_causal_engine(self):
        """Causal inference engine should import."""
        mod = importlib.import_module("causal_engine")
        assert hasattr(mod, "CausalSchemaEngine")
        assert hasattr(mod, "CausalMechanism")
        assert hasattr(mod, "CausalEdge")

    def test_import_schema_change_detector(self):
        """Schema change detector should import."""
        mod = importlib.import_module("schema_change_detector")
        assert hasattr(mod, "SchemaSnapshot")

    def test_import_webhook_alert(self):
        """Webhook alert module should import."""
        mod = importlib.import_module("webhook_alert")
        assert hasattr(mod, "WebhookAlert")

    def test_import_rule_engine(self):
        """Data quality rule engine should import."""
        mod = importlib.import_module("rule_engine")
        assert hasattr(mod, "DataQualityRuleEngine")

    def test_import_rules_engine(self):
        """Rules engine (YAML-driven) should import."""
        mod = importlib.import_module("rules_engine")
        assert hasattr(mod, "RulesEngine")

    def test_import_etl_cleaner(self):
        """ETL cleaner should import."""
        mod = importlib.import_module("etl_cleaner")
        assert hasattr(mod, "ETLCleaner")

    def test_import_compliance_engine(self):
        """Compliance engine should import."""
        mod = importlib.import_module("compliance_engine")
        assert hasattr(mod, "ComplianceEngine")

    def test_import_report_export(self):
        """Report exporter should import."""
        mod = importlib.import_module("report_export")
        assert hasattr(mod, "ReportExporter")

    def test_import_causal_mechanism_inferrer(self):
        """Causal mechanism inferrer should import."""
        mod = importlib.import_module("causal_mechanism_inferrer")
        assert hasattr(mod, "CausalMechanismInferrer")

    def test_import_generate_report(self):
        """Report generator should import."""
        mod = importlib.import_module("generate_report")

    def test_import_config_loader(self):
        """Config loader should import."""
        mod = importlib.import_module("config_loader")
        assert hasattr(mod, "QualityConfig")

    def test_import_gunicorn_conf(self):
        """Gunicorn config should import."""
        mod = importlib.import_module("gunicorn_conf")

    def test_import_etl_agent(self):
        """ETL clean agent should import."""
        mod = importlib.import_module("etl_agent")
        assert hasattr(mod, "ETLCleanAgent")

    def test_import_compliance_library(self):
        """Compliance library should import."""
        mod = importlib.import_module("compliance_library")
        assert hasattr(mod, "ComplianceLibrary")


# ============================================================
# Data Structure Tests
# ============================================================

class TestDataStructures:
    """Verify core data structures are correctly defined."""

    def test_causal_mechanism_enum(self):
        from causal_engine import CausalMechanism
        mechanisms = list(CausalMechanism)
        assert len(mechanisms) == 5
        expected = {"foreign_key", "etl_transform", "schema_dependency",
                    "quality_propagation", "cascading_failure"}
        assert {m.value for m in mechanisms} == expected

    def test_causal_edge_creation(self):
        from causal_engine import CausalEdge, CausalMechanism
        edge = CausalEdge(
            source_table="orders",
            source_column="customer_id",
            target_table="customers",
            target_column="id",
            mechanism=CausalMechanism.FOREIGN_KEY,
            strength=1.0,
            confidence=0.95,
        )
        assert edge.source_table == "orders"
        assert edge.strength == 1.0

    def test_schema_change_detector_classes(self):
        from schema_change_detector import ColumnDef, TableSchema
        col = ColumnDef(name="email", dtype="VARCHAR(255)")
        assert col.name == "email"
        assert col.to_dict() == {"name": "email", "dtype": "VARCHAR(255)"}


# ============================================================
# FastAPI App Tests (using httpx TestClient)
# ============================================================

class TestFastAPIEndpoints:
    """Smoke tests for key API endpoints."""

    @pytest.fixture
    def client(self):
        try:
            from fastapi.testclient import TestClient
            from app import app
            return TestClient(app)
        except ImportError:
            pytest.skip("httpx not installed for TestClient")

    def test_health_endpoint(self, client):
        """GET /health should return 200 with ok status."""
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data

    def test_metrics_endpoint(self, client):
        """GET /metrics should return 200."""
        resp = client.get("/metrics")
        assert resp.status_code == 200

    def test_summary_endpoint(self, client):
        """GET /summary should return valid structure."""
        resp = client.get("/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert "version" in data

    def test_tables_endpoint(self, client):
        """GET /tables should return a list."""
        resp = client.get("/tables")
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data

    def test_quality_trend_endpoint(self, client):
        """GET /quality/trend should return history."""
        resp = client.get("/quality/trend")
        assert resp.status_code == 200
        data = resp.json()
        assert "history" in data

    def test_lineage_endpoint(self, client):
        """GET /lineage should return lineage data."""
        resp = client.get("/lineage")
        assert resp.status_code == 200

    def test_predictive_endpoint(self, client):
        """GET /predictive should return analytics."""
        resp = client.get("/predictive")
        assert resp.status_code == 200

    def test_etl_strategies_endpoint(self, client):
        """GET /etl/strategies should list cleaning strategies."""
        resp = client.get("/etl/strategies")
        assert resp.status_code == 200
        data = resp.json()
        assert "strategies" in data

    def test_report_latest_endpoint(self, client):
        """GET /report/latest should return path info."""
        resp = client.get("/report/latest")
        assert resp.status_code == 200

    def test_docs_endpoint(self, client):
        """GET /docs should return Swagger UI."""
        resp = client.get("/docs")
        assert resp.status_code == 200

    def test_unauthorized_analysis(self, client):
        """POST /run/analysis without API key should return 401."""
        resp = client.post("/run/analysis")
        assert resp.status_code == 401


# ============================================================
# Utility Tests
# ============================================================

class TestUtilities:
    """Test utility functions."""

    def test_sliding_window_rate_limiter(self):
        from app import SlidingWindowRateLimiter
        limiter = SlidingWindowRateLimiter(window_sec=60, max_requests=3)
        assert limiter.is_allowed("test_key") is True
        assert limiter.is_allowed("test_key") is True
        assert limiter.is_allowed("test_key") is True
        assert limiter.is_allowed("test_key") is False
        assert limiter.remaining("test_key") == 0
        limiter.reset("test_key")
        assert limiter.remaining("test_key") == 3

    def test_load_json_nonexistent(self):
        from pathlib import Path
        from app import _load_json
        result = _load_json(Path("/nonexistent/path.json"))
        assert result == {}

    def test_security_headers(self):
        from app import SecurityHeadersMiddleware
        headers = SecurityHeadersMiddleware._security_headers()
        assert "Strict-Transport-Security" in headers
        assert "X-Content-Type-Options" in headers
        assert headers["X-Frame-Options"] == "DENY"
