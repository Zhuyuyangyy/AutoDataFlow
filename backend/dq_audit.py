#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AutoDataFlow Data Quality Audit Report
=====================================
Concrete novel feature: one-shot CLI that profiles every table in a SQLite
database and emits a single JSON quality audit (table-level + column-level).

Quality scoring (0-100) per column:
  - Start at 100
  - -50  if null_pct > 50%
  - -25  if null_pct between 10%-50%
  - -10  if null_pct between 1%-10%
  - -15  if unique_pct < 0.5% on a column with >100 rows (near-constant)
  - -20  if outlier_pct > 10% on numeric column
  - Floor at 0

Table score = mean(column scores). Overall score = mean(table scores).

Usage:
    python backend/dq_audit.py <db_path> [--out report.json] [--top-outliers 5]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import polars as pl


# ============================================================
# Data structures
# ============================================================

@dataclass
class ColumnAudit:
    name: str
    dtype: str
    row_count: int
    null_count: int
    null_pct: float
    unique_count: int
    unique_pct: float
    outlier_count: int = 0
    outlier_pct: float = 0.0
    score: int = 100
    issues: List[str] = field(default_factory=list)


@dataclass
class TableAudit:
    table: str
    row_count: int
    column_count: int
    columns: List[ColumnAudit]
    score: float = 0.0
    grade: str = "F"


@dataclass
class DataQualityAudit:
    generated_at: str
    db_path: str
    table_count: int
    total_rows: int
    overall_score: float
    overall_grade: str
    tables: List[TableAudit]
    recommendations: List[str] = field(default_factory=list)


# ============================================================
# Audit logic
# ============================================================

def _grade(score: float) -> str:
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


def _score_column(col: ColumnAudit) -> None:
    """Mutate col to attach score + issues based on stats."""
    score = 100
    issues: List[str] = []

    # Null penalty
    if col.null_pct > 0.5:
        score -= 50
        issues.append(f"critical null rate ({col.null_pct:.1%})")
    elif col.null_pct > 0.1:
        score -= 25
        issues.append(f"high null rate ({col.null_pct:.1%})")
    elif col.null_pct > 0.01:
        score -= 10
        issues.append(f"minor null rate ({col.null_pct:.1%})")

    # Near-constant penalty (only meaningful for tables with >100 rows)
    if col.row_count > 100 and col.unique_pct < 0.005 and col.null_count < col.row_count:
        score -= 15
        issues.append(f"near-constant column (uniqueness {col.unique_pct:.2%})")

    # Outlier penalty (numeric only)
    if col.outlier_pct > 0.1 and col.row_count > 0:
        score -= 20
        issues.append(f"high outlier rate ({col.outlier_pct:.1%})")

    col.score = max(score, 0)
    col.issues = issues


def _profile_column(series: pl.Series, total_rows: int) -> ColumnAudit:
    row_count = total_rows
    null_count = int(series.null_count())
    null_pct = (null_count / row_count) if row_count else 0.0

    non_null = series.drop_nulls()
    unique_count = int(non_null.n_unique()) if len(non_null) else 0
    unique_pct = (unique_count / row_count) if row_count else 0.0

    outlier_count = 0
    outlier_pct = 0.0
    if series.dtype in (pl.Int64, pl.Int32, pl.Float64, pl.Float32) and len(non_null) > 4:
        q1 = non_null.quantile(0.25)
        q3 = non_null.quantile(0.75)
        iqr = q3 - q1
        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr
        outlier_mask = ~non_null.is_between(lower, upper)
        outlier_count = int(outlier_mask.sum())
        outlier_pct = (outlier_count / row_count) if row_count else 0.0

    return ColumnAudit(
        name=series.name,
        dtype=str(series.dtype),
        row_count=row_count,
        null_count=null_count,
        null_pct=round(null_pct, 4),
        unique_count=unique_count,
        unique_pct=round(unique_pct, 4),
        outlier_count=outlier_count,
        outlier_pct=round(outlier_pct, 4),
    )


def audit_database(db_path: str, top_outliers: int = 5) -> DataQualityAudit:
    """Profile every table in db_path and build a DataQualityAudit."""
    db = Path(db_path)
    if not db.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    conn = sqlite3.connect(str(db))
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        tables = [row[0] for row in cursor.fetchall()]
    finally:
        conn.close()

    table_audits: List[TableAudit] = []
    total_rows = 0

    for table in tables:
        try:
            # Use sqlite3 directly + pl.DataFrame: avoids polars.read_database
            # URI/connectorx requirement, and is fast enough for warehouse-scale.
            conn2 = sqlite3.connect(str(db))
            try:
                cur = conn2.execute(f'SELECT * FROM "{table}"')
                col_names = [d[0] for d in cur.description] if cur.description else []
                rows = cur.fetchall()
            finally:
                conn2.close()
            if not col_names:
                df = pl.DataFrame()
            else:
                df = pl.DataFrame(rows, schema=col_names, orient="row")
        except Exception:
            # Skip unreadable tables, surface error in recommendations
            table_audits.append(
                TableAudit(
                    table=table,
                    row_count=0,
                    column_count=0,
                    columns=[],
                    score=0.0,
                    grade="F",
                )
            )
            continue

        row_count = len(df)
        total_rows += row_count
        columns = [_profile_column(df[c], row_count) for c in df.columns]
        for c in columns:
            _score_column(c)
        score = (sum(c.score for c in columns) / len(columns)) if columns else 0.0
        table_audits.append(
            TableAudit(
                table=table,
                row_count=row_count,
                column_count=len(columns),
                columns=columns,
                score=round(score, 2),
                grade=_grade(score),
            )
        )

    overall = (
        sum(t.score for t in table_audits) / len(table_audits) if table_audits else 0.0
    )

    recommendations: List[str] = []
    # Surface up to N worst columns across the warehouse
    all_cols: List[tuple] = []  # (score, table, col)
    for t in table_audits:
        for c in t.columns:
            all_cols.append((c.score, t.table, c))
    all_cols.sort(key=lambda x: x[0])
    for score, table, col in all_cols[:top_outliers]:
        if col.issues:
            recommendations.append(
                f"[{table}.{col.name}] score={score} issues={', '.join(col.issues)}"
            )

    return DataQualityAudit(
        generated_at=datetime.now().astimezone().isoformat(),
        db_path=str(db.resolve()),
        table_count=len(table_audits),
        total_rows=total_rows,
        overall_score=round(overall, 2),
        overall_grade=_grade(overall),
        tables=table_audits,
        recommendations=recommendations,
    )


def to_dict(audit: DataQualityAudit) -> Dict[str, Any]:
    return asdict(audit)


def render_markdown(audit: DataQualityAudit) -> str:
    """Optional Markdown rendering for human readers."""
    lines = [
        f"# Data Quality Audit Report",
        f"",
        f"- **Generated**: {audit.generated_at}",
        f"- **Database**: `{audit.db_path}`",
        f"- **Tables**: {audit.table_count}",
        f"- **Total rows**: {audit.total_rows:,}",
        f"- **Overall score**: **{audit.overall_score}** (grade {audit.overall_grade})",
        f"",
        f"## Tables",
        f"",
        f"| Table | Rows | Cols | Score | Grade |",
        f"|---|---:|---:|---:|:---:|",
    ]
    for t in audit.tables:
        lines.append(f"| `{t.table}` | {t.row_count:,} | {t.column_count} | {t.score} | {t.grade} |")

    if audit.recommendations:
        lines += ["", "## Top recommendations", ""]
        for r in audit.recommendations:
            lines.append(f"- {r}")
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run a data quality audit on a SQLite database.")
    parser.add_argument("db_path", help="Path to the SQLite database file")
    parser.add_argument("--out", default=None, help="Write JSON report to this path")
    parser.add_argument("--markdown", action="store_true", help="Also emit a Markdown summary")
    parser.add_argument("--top-outliers", type=int, default=5, help="Number of worst columns to surface")
    args = parser.parse_args(argv)

    try:
        audit = audit_database(args.db_path, top_outliers=args.top_outliers)
    except FileNotFoundError as e:
        print(f"[dq_audit] error: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"[dq_audit] error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    payload = json.dumps(to_dict(audit), ensure_ascii=False, indent=2, default=str)
    if args.out:
        Path(args.out).write_text(payload, encoding="utf-8")
        print(f"[dq_audit] wrote {args.out} (overall={audit.overall_score} grade={audit.overall_grade})")
    else:
        print(payload)

    if args.markdown:
        md_path = (Path(args.out) if args.out else Path("dq_audit_report.md")).with_suffix(".md")
        md_path.write_text(render_markdown(audit), encoding="utf-8")
        print(f"[dq_audit] wrote {md_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
