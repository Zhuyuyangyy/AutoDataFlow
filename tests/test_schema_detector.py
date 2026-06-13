"""
AutoDataFlow v3.0 - Schema Change Detector Tests
==================================================
Tests for schema_change_detector.py
"""

import sys
import os
import pytest
import sqlite3
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from schema_change_detector import SchemaSnapshot, ColumnDef, TableSchema, SchemaChange


@pytest.fixture
def sample_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE users (id INTEGER, name TEXT, email TEXT)")
    conn.execute("INSERT INTO users VALUES (1, 'Alice', 'alice@test.com')")
    conn.commit()
    conn.close()
    return db_path


class TestColumnDef:
    def test_creation(self):
        col = ColumnDef(name="email", dtype="TEXT")
        assert col.name == "email"
        assert col.dtype == "TEXT"

    def test_to_dict(self):
        col = ColumnDef(name="email", dtype="TEXT")
        d = col.to_dict()
        assert d == {"name": "email", "dtype": "TEXT"}

    def test_hash(self):
        c1 = ColumnDef(name="email", dtype="TEXT")
        c2 = ColumnDef(name="email", dtype="TEXT")
        assert hash(c1) == hash(c2)


class TestTableSchema:
    def test_creation(self):
        schema = TableSchema(
            table_name="users",
            columns=[ColumnDef("id", "INTEGER"), ColumnDef("name", "TEXT")],
            row_count=10,
            ddl_hash="abc123",
        )
        assert schema.table_name == "users"
        assert len(schema.columns) == 2

    def test_to_dict(self):
        schema = TableSchema(
            table_name="users",
            columns=[ColumnDef("id", "INTEGER")],
            row_count=5,
            ddl_hash="abc",
        )
        d = schema.to_dict()
        assert "table_name" in d
        assert "columns" in d

    def test_from_dict(self):
        d = {
            "table_name": "users",
            "columns": [{"name": "id", "dtype": "INTEGER"}],
            "row_count": 5,
            "ddl_hash": "abc",
        }
        schema = TableSchema.from_dict(d)
        assert schema.table_name == "users"
        assert len(schema.columns) == 1


class TestSchemaSnapshot:
    def test_detect_changes_initial(self, sample_db, tmp_path):
        detector = SchemaSnapshot(sample_db)
        changes = detector.detect_changes()
        # First run: should detect all tables as "added"
        assert isinstance(changes, list)

    def test_detect_changes_no_change(self, sample_db):
        detector = SchemaSnapshot(sample_db)
        detector.detect_changes()  # First run creates snapshot
        changes = detector.detect_changes()  # Second run should find no changes
        assert len(changes) == 0

    def test_detect_changes_add_column(self, sample_db):
        detector = SchemaSnapshot(sample_db)
        detector.detect_changes()  # Create initial snapshot

        # Add a column
        conn = sqlite3.connect(sample_db)
        conn.execute("ALTER TABLE users ADD COLUMN age INTEGER")
        conn.commit()
        conn.close()

        changes = detector.detect_changes()
        added = [c for c in changes if c.change_type == "column_added"]
        assert len(added) > 0

    def test_get_change_summary(self, sample_db):
        detector = SchemaSnapshot(sample_db)
        detector.detect_changes()
        summary = detector.get_change_summary()
        assert "total" in summary
        assert "has_breaking" in summary

    def test_get_timeline(self, sample_db):
        detector = SchemaSnapshot(sample_db)
        detector.detect_changes()
        timeline = detector.get_timeline()
        assert isinstance(timeline, list)

    def test_is_breaking_type_change(self, sample_db):
        detector = SchemaSnapshot(sample_db)
        assert detector._is_breaking_type_change("REAL", "INTEGER") is True
        assert detector._is_breaking_type_change("INTEGER", "REAL") is False
