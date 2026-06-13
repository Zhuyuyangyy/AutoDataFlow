"""
AutoDataFlow v3.0 - Causal Engine Tests
=========================================
Comprehensive tests for causal_engine.py and causal_mechanism_inferrer.py
"""

import sys
import os
import pytest
import sqlite3
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from causal_engine import (
    CausalEdge, CausalMechanism, SchemaNode, QualityMetric,
    DoOperation, CausalEffect, CounterfactualResult,
    CausalGraphBuilder, DoCalculusEngine, CounterfactualReasoner,
    CausalSchemaEngine,
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def sample_db(tmp_path):
    """Create a sample SQLite database for testing."""
    db_path = str(tmp_path / "test_warehouse.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE customers (
            id INTEGER PRIMARY KEY,
            name TEXT,
            email TEXT,
            region TEXT
        )
    """)
    for i in range(20):
        cursor.execute(
            "INSERT INTO customers VALUES (?, ?, ?, ?)",
            (i, f"Customer_{i}", f"cust{i}@example.com", ["North", "South", "East", "West"][i % 4])
        )

    cursor.execute("""
        CREATE TABLE orders (
            order_id INTEGER PRIMARY KEY,
            customer_id INTEGER,
            amount REAL,
            quantity INTEGER,
            date TEXT
        )
    """)
    for i in range(50):
        cursor.execute(
            "INSERT INTO orders VALUES (?, ?, ?, ?, ?)",
            (i, i % 20, round(100 + i * 10.5, 2), (i % 10) + 1, f"2024-01-{(i % 28) + 1:02d}")
        )

    cursor.execute("""
        CREATE TABLE products (
            product_id INTEGER PRIMARY KEY,
            name TEXT,
            price REAL,
            category TEXT
        )
    """)
    for i in range(30):
        cursor.execute(
            "INSERT INTO products VALUES (?, ?, ?, ?)",
            (i, f"Product_{i}", round(10 + i * 5.5, 2), ["Electronics", "Clothing", "Food"][i % 3])
        )

    conn.commit()
    conn.close()
    return db_path


# ============================================================
# Data Structure Tests
# ============================================================

class TestCausalEdge:
    def test_creation(self):
        edge = CausalEdge(
            source_table="orders",
            source_column="customer_id",
            target_table="customers",
            target_column="id",
            mechanism=CausalMechanism.FOREIGN_KEY.value,
            strength=0.95,
            confidence=0.9,
        )
        assert edge.source_table == "orders"
        assert edge.strength == 0.95
        assert edge.confidence == 0.9

    def test_to_dict(self):
        edge = CausalEdge(
            source_table="t1", source_column="c1",
            target_table="t2", target_column="c2",
            mechanism="foreign_key", strength=0.8, confidence=0.7,
        )
        d = edge.to_dict()
        assert d["source"] == "t1.c1"
        assert d["target"] == "t2.c2"
        assert d["mechanism"] == "foreign_key"

    def test_hash(self):
        e1 = CausalEdge("t1", "c1", "t2", "c2", "foreign_key")
        e2 = CausalEdge("t1", "c1", "t2", "c2", "foreign_key")
        assert hash(e1) == hash(e2)


class TestDoOperation:
    def test_drop_column(self):
        op = DoOperation(op_type="drop_column", table="orders", column="email")
        assert op.is_destructive()
        assert "drop_column" in str(op)

    def test_rename_column(self):
        op = DoOperation(op_type="rename_column", table="orders", column="amount", new_name="revenue")
        assert not op.is_destructive()
        assert "revenue" in str(op)

    def test_add_column(self):
        op = DoOperation(op_type="add_column", table="orders", column="new_col", new_dtype="TEXT")
        assert not op.is_destructive()

    def test_truncate_table(self):
        op = DoOperation(op_type="truncate_table", table="orders")
        assert op.is_destructive()


class TestSchemaNode:
    def test_node_id_with_column(self):
        node = SchemaNode(table="orders", column="amount")
        assert node.node_id() == "orders.amount"

    def test_node_id_table_only(self):
        node = SchemaNode(table="orders")
        assert node.node_id() == "orders"


class TestCausalMechanism:
    def test_all_mechanisms(self):
        mechanisms = list(CausalMechanism)
        assert len(mechanisms) == 5
        values = {m.value for m in mechanisms}
        assert "foreign_key" in values
        assert "etl_transform" in values
        assert "cascading_failure" in values


# ============================================================
# CausalGraphBuilder Tests
# ============================================================

class TestCausalGraphBuilder:
    def test_build_from_db(self, sample_db):
        builder = CausalGraphBuilder(sample_db, use_statistical_inference=False)
        builder.build_from_db()
        assert len(builder.nodes) > 0
        # Should have table and column nodes
        table_nodes = [n for n in builder.nodes.values() if n.node_type == "table"]
        assert len(table_nodes) == 3

    def test_graph_dict_export(self, sample_db):
        builder = CausalGraphBuilder(sample_db, use_statistical_inference=False)
        builder.build_from_db()
        graph = builder.to_graph_dict()
        assert "nodes" in graph
        assert "edges" in graph
        assert "stats" in graph

    def test_downstream_traversal(self, sample_db):
        builder = CausalGraphBuilder(sample_db, use_statistical_inference=False)
        builder.build_from_db()
        # Add a test edge
        builder.edges.add(CausalEdge(
            source_table="orders", source_column="customer_id",
            target_table="customers", target_column="id",
            mechanism="foreign_key",
        ))
        downstream = builder.get_downstream("orders.customer_id")
        assert "customers.id" in downstream

    def test_upstream_traversal(self, sample_db):
        builder = CausalGraphBuilder(sample_db, use_statistical_inference=False)
        builder.build_from_db()
        builder.edges.add(CausalEdge(
            source_table="orders", source_column="customer_id",
            target_table="customers", target_column="id",
            mechanism="foreign_key",
        ))
        upstream = builder.get_upstream("customers.id")
        assert "orders.customer_id" in upstream


# ============================================================
# DoCalculusEngine Tests
# ============================================================

class TestDoCalculusEngine:
    def test_predict_impact_drop(self, sample_db):
        builder = CausalGraphBuilder(sample_db, use_statistical_inference=False)
        builder.build_from_db()
        engine = DoCalculusEngine(builder)

        op = DoOperation(op_type="drop_column", table="orders", column="customer_id")
        effects = engine.predict_impact(op)
        assert isinstance(effects, list)
        # All effects should have probability and severity
        for e in effects:
            assert 0 <= e.probability <= 1
            assert e.severity in ("info", "warning", "critical")

    def test_predict_impact_rename(self, sample_db):
        builder = CausalGraphBuilder(sample_db, use_statistical_inference=False)
        builder.build_from_db()
        engine = DoCalculusEngine(builder)

        op = DoOperation(op_type="rename_column", table="orders", column="amount", new_name="revenue")
        effects = engine.predict_impact(op)
        assert isinstance(effects, list)


# ============================================================
# CausalSchemaEngine (Top-Level API) Tests
# ============================================================

class TestCausalSchemaEngine:
    def test_build_causal_graph(self, sample_db):
        engine = CausalSchemaEngine(sample_db)
        engine.build_causal_graph()
        assert engine._built is True
        assert engine.graph_builder is not None

    def test_predict_impact(self, sample_db):
        engine = CausalSchemaEngine(sample_db)
        impacts = engine.predict_impact("drop_column", "orders", "customer_id")
        assert isinstance(impacts, list)

    def test_explain_change_risk(self, sample_db):
        engine = CausalSchemaEngine(sample_db)
        report = engine.explain_change_risk("drop_column", "orders", "customer_id")
        assert "operation" in report
        assert "risk_level" in report
        assert report["risk_level"] in ("CRITICAL", "HIGH", "LOW", "MEDIUM")

    def test_get_causal_graph(self, sample_db):
        engine = CausalSchemaEngine(sample_db)
        graph = engine.get_causal_graph()
        assert "nodes" in graph
        assert "edges" in graph

    def test_counterfactual(self, sample_db):
        engine = CausalSchemaEngine(sample_db)
        result = engine.counterfactual(
            change={"type": "rename_column", "table": "orders", "column": "amount", "new_name": "revenue"},
            outcome="ETL_RevenueAggregation"
        )
        assert result.question is not None
        assert result.factual is not None
        assert result.counterfactual is not None
