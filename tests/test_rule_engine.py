"""
AutoDataFlow v3.0 - Rule Engine Tests
=======================================
Tests for rule_engine.py and rules_engine.py
"""

import sys
import os
import pytest
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import polars as pl


@pytest.fixture
def sample_df():
    return pl.DataFrame({
        "id": [1, 2, 3, 4, 5],
        "email": ["a@test.com", "b@test.com", None, "invalid", "e@test.com"],
        "amount": [100.0, 200.0, 300.0, 400.0, 500.0],
        "status": ["active", "active", "inactive", "active", "pending"],
        "date": ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"],
    })


# ============================================================
# RuleExecutor Tests (rule_engine.py)
# ============================================================

class TestRuleExecutor:
    def test_null_check_pass(self, sample_df):
        from rule_engine import RuleExecutor
        rule = {"id": "test", "type": "null_check", "field": "id", "severity": "critical"}
        passed, violation = RuleExecutor.execute(sample_df, rule, "test_table")
        assert passed is True
        assert violation is None

    def test_null_check_fail(self, sample_df):
        from rule_engine import RuleExecutor
        rule = {"id": "test", "type": "null_check", "field": "email", "severity": "critical"}
        passed, violation = RuleExecutor.execute(sample_df, rule, "test_table")
        assert passed is False
        assert violation.failed_count == 1

    def test_regex_match_pass(self, sample_df):
        from rule_engine import RuleExecutor
        rule = {
            "id": "test", "type": "regex_match", "field": "email",
            "pattern": r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$",
            "severity": "critical"
        }
        # Should fail because "invalid" doesn't match
        passed, violation = RuleExecutor.execute(sample_df, rule, "test_table")
        assert passed is False

    def test_range_check_pass(self, sample_df):
        from rule_engine import RuleExecutor
        rule = {"id": "test", "type": "range_check", "field": "amount", "min": 0, "max": 1000, "severity": "critical"}
        passed, violation = RuleExecutor.execute(sample_df, rule, "test_table")
        assert passed is True

    def test_range_check_fail(self):
        from rule_engine import RuleExecutor
        df = pl.DataFrame({"val": [50, 150, 250, -10]})
        rule = {"id": "test", "type": "range_check", "field": "val", "min": 0, "max": 200, "severity": "critical"}
        passed, violation = RuleExecutor.execute(df, rule, "test_table")
        assert passed is False

    def test_uniqueness_pass(self, sample_df):
        from rule_engine import RuleExecutor
        rule = {"id": "test", "type": "uniqueness", "field": "id", "severity": "critical"}
        passed, violation = RuleExecutor.execute(sample_df, rule, "test_table")
        assert passed is True

    def test_uniqueness_fail(self):
        from rule_engine import RuleExecutor
        df = pl.DataFrame({"id": [1, 2, 2, 3, 3]})
        rule = {"id": "test", "type": "uniqueness", "field": "id", "severity": "critical"}
        passed, violation = RuleExecutor.execute(df, rule, "test_table")
        assert passed is False
        assert violation.failed_count == 2

    def test_enum_check_pass(self, sample_df):
        from rule_engine import RuleExecutor
        rule = {
            "id": "test", "type": "enum_check", "field": "status",
            "allowed_values": ["active", "inactive", "pending"],
            "severity": "warning"
        }
        passed, violation = RuleExecutor.execute(sample_df, rule, "test_table")
        assert passed is True

    def test_enum_check_fail(self):
        from rule_engine import RuleExecutor
        df = pl.DataFrame({"status": ["active", "unknown", "inactive"]})
        rule = {
            "id": "test", "type": "enum_check", "field": "status",
            "allowed_values": ["active", "inactive"],
            "severity": "warning"
        }
        passed, violation = RuleExecutor.execute(df, rule, "test_table")
        assert passed is False

    def test_unknown_field(self, sample_df):
        from rule_engine import RuleExecutor
        rule = {"id": "test", "type": "null_check", "field": "nonexistent", "severity": "critical"}
        passed, violation = RuleExecutor.execute(sample_df, rule, "test_table")
        assert passed is True

    def test_unknown_rule_type(self, sample_df):
        from rule_engine import RuleExecutor
        rule = {"id": "test", "type": "nonexistent_type", "field": "id", "severity": "critical"}
        passed, violation = RuleExecutor.execute(sample_df, rule, "test_table")
        assert passed is True


# ============================================================
# RulesEngine Tests (rules_engine.py)
# ============================================================

class TestRulesEngine:
    def test_check_data(self, tmp_path):
        from rules_engine import RulesEngine
        db_path = str(tmp_path / "test.db")
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE t (id INTEGER, name TEXT)")
        conn.commit()
        conn.close()

        engine = RulesEngine(db_path, Path(__file__).parent.parent / "backend")
        data = [
            {"id": 1, "name": "Alice"},
            {"id": 2, "name": "Bob"},
        ]
        result = engine.check_data(data)
        assert "violations" in result
        assert "quality_score" in result

    def test_add_and_remove_rule(self, tmp_path):
        from rules_engine import RulesEngine
        db_path = str(tmp_path / "test.db")
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.commit()
        conn.close()

        engine = RulesEngine(db_path, Path(__file__).parent.parent / "backend")
        result = engine.add_rule("t", {"type": "null_check", "field": "id", "severity": "critical"})
        assert result["status"] == "added"
        assert "rule_id" in result

        remove_result = engine.remove_rule(result["rule_id"])
        assert remove_result["status"] == "removed"


# ============================================================
# DataQualityRuleEngine Tests (rule_engine.py)
# ============================================================

class TestDataQualityRuleEngine:
    def test_validate_table_nonexistent(self, tmp_path):
        from rule_engine import DataQualityRuleEngine
        db_path = str(tmp_path / "test.db")
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.commit()
        conn.close()

        cfg = Path(__file__).parent.parent / "backend" / "config"
        engine = DataQualityRuleEngine(db_path, cfg)
        result = engine.validate_table("nonexistent_table")
        assert result.quality_score == 0
        assert result.passed == 0
