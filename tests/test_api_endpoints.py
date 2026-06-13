"""
AutoDataFlow v3.0 - API Endpoint Tests
========================================
Comprehensive tests for all FastAPI endpoints.
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app import app
    return TestClient(app)


# ============================================================
# Health & Metrics
# ============================================================

class TestHealthEndpoints:
    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data

    def test_metrics(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200


# ============================================================
# Dashboard Endpoints
# ============================================================

class TestDashboardEndpoints:
    def test_summary(self, client):
        resp = client.get("/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert "version" in data
        assert "total_tables" in data

    def test_tables(self, client):
        resp = client.get("/tables")
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert "tables" in data

    def test_quality_trend(self, client):
        resp = client.get("/quality/trend")
        assert resp.status_code == 200
        data = resp.json()
        assert "history" in data

    def test_lineage(self, client):
        resp = client.get("/lineage")
        assert resp.status_code == 200

    def test_predictive(self, client):
        resp = client.get("/predictive")
        assert resp.status_code == 200


# ============================================================
# Schema Endpoints
# ============================================================

class TestSchemaEndpoints:
    def test_schema_changes(self, client):
        resp = client.get("/schema/changes")
        assert resp.status_code == 200

    def test_schema_timeline(self, client):
        resp = client.get("/schema/changes/timeline")
        assert resp.status_code == 200


# ============================================================
# Rule Engine Endpoints
# ============================================================

class TestRuleEndpoints:
    def test_etl_strategies(self, client):
        resp = client.get("/etl/strategies")
        assert resp.status_code == 200
        data = resp.json()
        assert "strategies" in data
        assert len(data["strategies"]) > 0


# ============================================================
# Report Endpoints
# ============================================================

class TestReportEndpoints:
    def test_report_latest(self, client):
        resp = client.get("/report/latest")
        assert resp.status_code == 200


# ============================================================
# Alert Endpoints
# ============================================================

class TestAlertEndpoints:
    def test_alert_channels(self, client):
        resp = client.get("/alerts/channels")
        assert resp.status_code == 200
        data = resp.json()
        assert "config" in data


# ============================================================
# Security Tests
# ============================================================

class TestSecurity:
    def test_unauthorized_analysis(self, client):
        resp = client.post("/run/analysis")
        assert resp.status_code == 401

    def test_docs_accessible(self, client):
        resp = client.get("/docs")
        assert resp.status_code == 200

    def test_redoc_accessible(self, client):
        resp = client.get("/redoc")
        assert resp.status_code == 200

    def test_request_id_in_response(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "request_id" in data

    def test_security_headers(self, client):
        resp = client.get("/health")
        assert "x-content-type-options" in resp.headers
        assert resp.headers["x-content-type-options"] == "nosniff"
