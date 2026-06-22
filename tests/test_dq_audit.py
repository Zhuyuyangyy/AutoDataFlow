"""
AutoDataFlow - Data Quality Audit Tests
========================================
Tests for the novel dq_audit.py feature.
"""

import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from dq_audit import (
    ColumnAudit,
    DataQualityAudit,
    TableAudit,
    audit_database,
    render_markdown,
    to_dict,
    _grade,
    _score_column,
)


@pytest.fixture
def sample_db(tmp_path):
    db_path = str(tmp_path / "warehouse.db")
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    # table_a: clean
    c.execute("CREATE TABLE clean_orders (id INTEGER PRIMARY KEY, amount REAL, region TEXT)")
    for i in range(50):
        c.execute(
            "INSERT INTO clean_orders VALUES (?, ?, ?)",
            (i, 100.0 + i, ["N", "S", "E", "W"][i % 4]),
        )
    # table_b: high nulls + outliers
    c.execute("CREATE TABLE dirty_logs (id INTEGER, value REAL, tag TEXT)")
    rows = [(i, (1000.0 if i == 1 else float(i)), "x") for i in range(30)]
    # Force many nulls
    rows_nulled = []
    for i, v, t in rows:
        if i % 3 == 0:
            rows_nulled.append((i, None, None))
        else:
            rows_nulled.append((i, v, t))
    c.executemany("INSERT INTO dirty_logs VALUES (?, ?, ?)", rows_nulled)
    conn.commit()
    conn.close()
    return db_path


class TestScoring:
    def test_grade_thresholds(self):
        assert _grade(95) == "A"
        assert _grade(85) == "B"
        assert _grade(75) == "C"
        assert _grade(65) == "D"
        assert _grade(40) == "F"

    def test_score_column_high_nulls(self):
        col = ColumnAudit(
            name="x", dtype="Int64", row_count=100,
            null_count=60, null_pct=0.6,
            unique_count=40, unique_pct=0.4,
        )
        _score_column(col)
        assert col.score == 50  # 100 - 50 (critical null)
        assert any("null" in i.lower() for i in col.issues)

    def test_score_column_no_penalties(self):
        col = ColumnAudit(
            name="x", dtype="Int64", row_count=100,
            null_count=0, null_pct=0.0,
            unique_count=100, unique_pct=1.0,
        )
        _score_column(col)
        assert col.score == 100
        assert col.issues == []

    def test_score_column_floor_zero(self):
        col = ColumnAudit(
            name="x", dtype="Int64", row_count=100,
            null_count=99, null_pct=0.99,
            unique_count=1, unique_pct=0.01,
        )
        _score_column(col)
        assert col.score >= 0


class TestAuditDatabase:
    def test_audit_runs(self, sample_db):
        audit = audit_database(sample_db)
        assert isinstance(audit, DataQualityAudit)
        assert audit.table_count == 2
        assert audit.total_rows == 80  # 50 + 30

    def test_dirty_table_lower_score(self, sample_db):
        audit = audit_database(sample_db)
        by_name = {t.table: t for t in audit.tables}
        assert by_name["clean_orders"].score > by_name["dirty_logs"].score
        # overall grade exists
        assert audit.overall_grade in ("A", "B", "C", "D", "F")

    def test_recommendations_present(self, sample_db):
        audit = audit_database(sample_db)
        assert isinstance(audit.recommendations, list)

    def test_to_dict_is_jsonable(self, sample_db):
        audit = audit_database(sample_db)
        payload = to_dict(audit)
        # round-trip JSON
        text = json.dumps(payload, default=str)
        again = json.loads(text)
        assert again["table_count"] == 2


class TestMarkdown:
    def test_markdown_contains_summary(self, sample_db):
        audit = audit_database(sample_db)
        md = render_markdown(audit)
        assert "# Data Quality Audit Report" in md
        assert "Overall score" in md
        assert "clean_orders" in md
        assert "dirty_logs" in md

    def test_markdown_recommendations_section(self, sample_db):
        audit = audit_database(sample_db, top_outliers=3)
        md = render_markdown(audit)
        if audit.recommendations:
            assert "## Top recommendations" in md


class TestCLI:
    def test_cli_runs_and_writes(self, sample_db, tmp_path, capsys):
        out_path = tmp_path / "report.json"
        from dq_audit import main as cli_main
        rc = cli_main([sample_db, "--out", str(out_path)])
        assert rc == 0
        assert out_path.exists()
        data = json.loads(out_path.read_text(encoding="utf-8"))
        assert "overall_score" in data
        captured = capsys.readouterr()
        assert "wrote" in captured.out.lower() or "overall" in captured.out.lower()

    def test_cli_missing_file(self, tmp_path, capsys):
        from dq_audit import main as cli_main
        rc = cli_main([str(tmp_path / "nope.db")])
        assert rc == 2
