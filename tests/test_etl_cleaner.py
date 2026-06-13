"""
AutoDataFlow v3.0 - ETL Cleaner Tests
=======================================
Tests for etl_cleaner.py and etl_agent.py
"""

import sys
import os
import pytest
import sqlite3
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import polars as pl


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def sample_df():
    return pl.DataFrame({
        "id": [1, 2, 3, 4, 5],
        "name": ["Alice", "Bob", None, "Diana", "Eve"],
        "amount": [100.0, 200.0, 5000.0, 150.0, -50.0],
        "email": ["a@test.com", "b@test.com", "c@test.com", "d@test.com", "e@test.com"],
        "date": ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"],
    })


@pytest.fixture
def sample_db_with_tables(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE test_sales (
            id INTEGER, name TEXT, amount REAL, region TEXT
        )
    """)
    for i in range(20):
        conn.execute(
            "INSERT INTO test_sales VALUES (?, ?, ?, ?)",
            (i, f"Name_{i}", round(100 + i * 10.5, 2), ["A", "B", "C"][i % 3])
        )
    conn.commit()
    conn.close()
    return db_path


# ============================================================
# CleaningStrategy Tests
# ============================================================

class TestCleaningStrategy:
    def test_drop_nulls(self, sample_df):
        from etl_cleaner import CleaningStrategy
        result, log, modified = CleaningStrategy.apply(sample_df, ["drop_nulls"])
        assert len(result) == 4  # One null row removed
        assert any("drop_nulls" in l for l in log)

    def test_fill_null_median(self, sample_df):
        from etl_cleaner import CleaningStrategy
        result, log, modified = CleaningStrategy.apply(sample_df, ["fill_null_median"])
        assert any("fill_null_median" in l for l in log)

    def test_fill_null_mean(self, sample_df):
        from etl_cleaner import CleaningStrategy
        result, log, modified = CleaningStrategy.apply(sample_df, ["fill_null_mean"])
        assert any("fill_null_mean" in l for l in log)

    def test_fill_null_zero(self, sample_df):
        from etl_cleaner import CleaningStrategy
        result, log, modified = CleaningStrategy.apply(sample_df, ["fill_null_zero"])
        assert any("fill_null_zero" in l for l in log)

    def test_outlier_clip(self, sample_df):
        from etl_cleaner import CleaningStrategy
        result, log, modified = CleaningStrategy.apply(sample_df, ["outlier_clip"])
        assert any("outlier_clip" in l for l in log)

    def test_outlier_flag(self, sample_df):
        from etl_cleaner import CleaningStrategy
        result, log, modified = CleaningStrategy.apply(sample_df, ["outlier_flag"])
        assert any("outlier_flag" in l for l in log)

    def test_outlier_winsorize(self, sample_df):
        from etl_cleaner import CleaningStrategy
        result, log, modified = CleaningStrategy.apply(sample_df, ["outlier_winsorize"])
        assert any("outlier_winsorize" in l for l in log)

    def test_deduplicate(self):
        from etl_cleaner import CleaningStrategy
        df = pl.DataFrame({"id": [1, 1, 2, 3], "val": ["a", "b", "c", "d"]})
        result, log, modified = CleaningStrategy.apply(df, ["deduplicate"])
        assert len(result) == 3

    def test_trim_strings(self):
        from etl_cleaner import CleaningStrategy
        df = pl.DataFrame({"name": ["  Alice  ", " Bob ", "Charlie"]})
        result, log, modified = CleaningStrategy.apply(df, ["trim_strings"])
        assert result["name"][0] == "Alice"

    def test_flag_nulls(self, sample_df):
        from etl_cleaner import CleaningStrategy
        result, log, modified = CleaningStrategy.apply(sample_df, ["flag_nulls"])
        assert any("_is_null" in c for c in result.columns)

    def test_unknown_strategy(self, sample_df):
        from etl_cleaner import CleaningStrategy
        result, log, modified = CleaningStrategy.apply(sample_df, ["nonexistent_strategy"])
        assert any("unknown_strategy" in l for l in log)

    def test_multiple_strategies(self, sample_df):
        from etl_cleaner import CleaningStrategy
        result, log, modified = CleaningStrategy.apply(sample_df, ["drop_nulls", "outlier_clip", "trim_strings"])
        assert len(log) >= 2


# ============================================================
# QualityScorer Tests
# ============================================================

class TestQualityScorer:
    def test_simple_score(self, sample_df):
        from etl_cleaner import QualityScorer
        score = QualityScorer.simple_score(sample_df)
        assert 0 <= score <= 100

    def test_simple_score_empty(self):
        from etl_cleaner import QualityScorer
        df = pl.DataFrame({"a": []})
        score = QualityScorer.simple_score(df)
        assert score == 0.0


# ============================================================
# ETLCleaner Tests
# ============================================================

class TestETLCleaner:
    def test_clean_single(self, sample_db_with_tables, tmp_path):
        from etl_cleaner import ETLCleaner
        cfg = Path(__file__).parent.parent / "backend" / "config"
        cleaner = ETLCleaner(sample_db_with_tables, cfg)
        op = cleaner.clean_single("test_sales", "test_sales_cleaned", ["drop_nulls", "trim_strings"])
        assert op.before_count == 20
        assert op.target == "test_sales_cleaned"

    def test_clean_multi_target(self, sample_db_with_tables, tmp_path):
        from etl_cleaner import ETLCleaner
        cfg = Path(__file__).parent.parent / "backend" / "config"
        cleaner = ETLCleaner(sample_db_with_tables, cfg)
        result = cleaner.clean(
            source_table="test_sales",
            targets=[
                {"name": "dwd_sales", "rules": ["drop_nulls", "outlier_clip"]},
                {"name": "ads_sales", "rules": ["deduplicate", "fill_null_median"]},
            ]
        )
        assert result.source_table == "test_sales"
        assert len(result.targets) == 2
        assert result.overall_quality_score >= 0


# ============================================================
# ETL Agent Tests
# ============================================================

class TestETLAgent:
    def test_clean_column_numeric(self):
        from auto_data_flow import ETLAgent
        import polars as pl
        agent = ETLAgent()
        s = pl.Series("amount", [1.0, None, 3.0, None, 5.0])
        cleaned = agent.clean_column(s, strategy="median")
        assert cleaned.null_count() == 0

    def test_clean_column_string(self):
        from auto_data_flow import ETLAgent
        import polars as pl
        agent = ETLAgent()
        s = pl.Series("name", ["Alice", None, "Charlie"])
        cleaned = agent.clean_column(s)
        assert cleaned.null_count() == 0

    def test_detect_outliers(self):
        from auto_data_flow import ETLAgent
        import polars as pl
        agent = ETLAgent()
        s = pl.Series("val", [1, 2, 3, 4, 5, 100])
        mask = agent.detect_outliers_iqr(s)
        # 100 should be an outlier
        assert mask.sum() < len(s)

    def test_profile_column(self):
        from auto_data_flow import ETLAgent
        import polars as pl
        agent = ETLAgent()
        s = pl.Series("test", [1, 2, 3, None, 5])
        profile = agent.profile_column(s)
        assert profile.null_count == 1
        assert profile.unique_count == 4
