#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AutoDataFlow 可配置数据质量规则引擎 (rules_engine.py)
====================================================
独立规则引擎：从 rules.yaml 加载规则，提供 CRUD API。

规则类型：null_check / regex_match / range_check / uniqueness /
          freshness / enum_check
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
import polars as pl
import yaml


# ============================================================
# 1. 数据结构
# ============================================================

class Severity(str):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


@dataclass
class RuleViolation:
    rule_id: str
    rule_type: str
    table: str
    field: str
    severity: str
    message: str
    failed_count: int
    total_count: int
    sample_values: List[Any] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "rule_type": self.rule_type,
            "table": self.table,
            "field": self.field,
            "severity": self.severity,
            "message": self.message,
            "failed_count": self.failed_count,
            "total_count": self.total_count,
            "sample_values": [str(v) for v in self.sample_values[:5]],
        }


@dataclass
class TableRuleResult:
    table: str
    total_rules: int
    passed: int
    failed: int
    violations: List[RuleViolation]
    quality_score: float
    passed_rules: List[str]
    failed_rules: List[str]

    def to_dict(self) -> dict:
        return {
            "table": self.table,
            "total_rules": self.total_rules,
            "passed": self.passed,
            "failed": self.failed,
            "quality_score": round(self.quality_score, 1),
            "violations": [v.to_dict() for v in self.violations],
            "passed_rules": self.passed_rules,
            "failed_rules": self.failed_rules,
        }


@dataclass
class GlobalRuleResult:
    total_tables: int
    total_rules: int
    total_passed: int
    total_failed: int
    overall_score: float
    table_results: List[TableRuleResult]
    critical_violations: List[dict]
    summary_by_severity: Dict[str, int]

    def to_dict(self) -> dict:
        return {
            "total_tables": self.total_tables,
            "total_rules": self.total_rules,
            "total_passed": self.total_passed,
            "total_failed": self.total_failed,
            "overall_score": round(self.overall_score, 1),
            "table_results": [r.to_dict() for r in self.table_results],
            "critical_violations": self.critical_violations,
            "summary_by_severity": self.summary_by_severity,
        }


# ============================================================
# 2. 规则加载器（从 rules.yaml）
# ============================================================

def load_rules_from_yaml(config_path: Path) -> Dict[str, Any]:
    """从 rules.yaml 加载规则配置"""
    if not config_path.exists():
        return {"data_quality_rules": {"defaults": [], "table_rules": {}}}
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_rules_for_table(config: Dict[str, Any], table_name: str) -> List[Dict]:
    """获取某个表适用的所有规则（defaults + 表级 + 通配符）"""
    dqr = config.get("data_quality_rules", {})
    defaults = dqr.get("defaults", [])
    table_rules = dqr.get("table_rules", {})

    rules = []
    wildcard = table_rules.get("*", [])
    rules.extend([{**r, "applies_to": "*"} for r in wildcard if r.get("enabled", True)])
    specific = table_rules.get(table_name, [])
    rules.extend([{**r, "applies_to": table_name} for r in specific if r.get("enabled", True)])
    rules.extend([{**r, "applies_to": "defaults"} for r in defaults if r.get("enabled", True)])
    return rules


# ============================================================
# 3. 规则执行器（Polars 驱动）
# ============================================================

def execute_rule(df: pl.DataFrame, rule: Dict, table: str) -> Tuple[bool, Optional[RuleViolation]]:
    """执行单条规则，返回 (passed, violation_or_None)"""
    rule_id = rule.get("id", "unknown")
    rule_type = rule.get("type", "")
    field = rule.get("field", "")
    severity = rule.get("severity", "warning")

    if not field and rule_type not in ("uniqueness",):
        return True, None

    total_count = len(df)

    try:
        if rule_type == "null_check":
            return _null_check(df, rule, table, total_count)
        elif rule_type == "regex_match":
            return _regex_match(df, rule, table, total_count)
        elif rule_type == "range_check":
            return _range_check(df, rule, table, total_count)
        elif rule_type == "uniqueness":
            return _uniqueness(df, rule, table, total_count)
        elif rule_type == "freshness_check":
            return _freshness_check(df, rule, table, total_count)
        elif rule_type == "enum_check":
            return _enum_check(df, rule, table, total_count)
        else:
            return True, None
    except Exception as e:
        return False, RuleViolation(
            rule_id=rule_id, rule_type=rule_type, table=table, field=field,
            severity=severity, message=f"规则执行异常: {e}",
            failed_count=0, total_count=total_count,
        )


def _null_check(df: pl.DataFrame, rule: Dict, table: str, total: int) -> Tuple[bool, Optional[RuleViolation]]:
    field = rule.get("field", "")
    if field not in df.columns:
        return True, None
    null_count = df[field].null_count()
    failed = null_count
    if failed == 0:
        return True, None
    sample = df.filter(pl.col(field).is_null())[field].head(5).to_list()
    return False, RuleViolation(
        rule_id=rule.get("id"), rule_type="null_check", table=table, field=field,
        severity=rule.get("severity", "critical"),
        message=f"字段 {field} 存在 {failed}/{total} 个空值",
        failed_count=failed, total_count=total, sample_values=sample,
    )


def _regex_match(df: pl.DataFrame, rule: Dict, table: str, total: int) -> Tuple[bool, Optional[RuleViolation]]:
    field = rule.get("field", "")
    pattern = rule.get("pattern", "")
    if field not in df.columns:
        return True, None
    non_null = df.filter(pl.col(field).is_not_null())
    if len(non_null) == 0:
        return True, None
    try:
        regex = re.compile(pattern)
        mask = non_null[field].cast(pl.Utf8).map_elements(
            lambda v: bool(regex.match(str(v))), return_dtype=pl.Boolean
        )
        failed = (~mask).sum()
    except Exception:
        return True, None
    if failed == 0:
        return True, None
    sample = non_null.filter(~mask)[field].head(5).to_list()
    return False, RuleViolation(
        rule_id=rule.get("id"), rule_type="regex_match", table=table, field=field,
        severity=rule.get("severity", "critical"),
        message=f"字段 {field} 有 {failed} 个值不匹配正则: {pattern}",
        failed_count=failed, total_count=len(non_null), sample_values=sample,
    )


def _range_check(df: pl.DataFrame, rule: Dict, table: str, total: int) -> Tuple[bool, Optional[RuleViolation]]:
    field = rule.get("field", "")
    min_val = rule.get("min")
    max_val = rule.get("max")
    if field not in df.columns:
        return True, None
    non_null = df.filter(pl.col(field).is_not_null())
    if len(non_null) == 0:
        return True, None
    mask = None
    if min_val is not None:
        mask = non_null[field] >= min_val
    if max_val is not None:
        m = non_null[field] <= max_val
        mask = mask & m if mask is not None else m
    failed = (~mask).sum() if mask is not None else 0
    if failed == 0:
        return True, None
    sample = non_null.filter(~mask)[field].head(5).to_list()
    return False, RuleViolation(
        rule_id=rule.get("id"), rule_type="range_check", table=table, field=field,
        severity=rule.get("severity", "critical"),
        message=f"字段 {field} 有 {failed} 个值超出范围 [{min_val}, {max_val}]",
        failed_count=failed, total_count=len(non_null), sample_values=sample,
    )


def _uniqueness(df: pl.DataFrame, rule: Dict, table: str, total: int) -> Tuple[bool, Optional[RuleViolation]]:
    field = rule.get("field", "")
    if field not in df.columns:
        return True, None
    non_null = df.filter(pl.col(field).is_not_null())
    if len(non_null) == 0:
        return True, None
    dup_mask = non_null[field].is_duplicated()
    failed = dup_mask.sum()
    if failed == 0:
        return True, None
    sample = non_null.filter(dup_mask)[field].head(5).to_list()
    return False, RuleViolation(
        rule_id=rule.get("id"), rule_type="uniqueness", table=table, field=field,
        severity=rule.get("severity", "critical"),
        message=f"字段 {field} 存在 {failed} 个重复值",
        failed_count=failed, total_count=len(non_null), sample_values=sample,
    )


def _freshness_check(df: pl.DataFrame, rule: Dict, table: str, total: int) -> Tuple[bool, Optional[RuleViolation]]:
    ts_field = rule.get("timestamp_field", "date")
    max_age = rule.get("max_age_minutes", 60)
    if ts_field not in df.columns:
        return True, None
    non_null = df.filter(pl.col(ts_field).is_not_null())
    if len(non_null) == 0:
        return True, None
    now = datetime.now()
    cutoff = now - timedelta(minutes=max_age)
    try:
        ts_col = non_null[ts_field].str.to_datetime("%Y-%m-%d", strict=False)
    except Exception:
        return True, None
    old_rows = ts_col < cutoff
    failed = old_rows.sum()
    if failed == 0:
        return True, None
    sample = non_null.filter(old_rows)[ts_field].head(5).to_list()
    return False, RuleViolation(
        rule_id=rule.get("id"), rule_type="freshness_check", table=table, field=ts_field,
        severity=rule.get("severity", "warning"),
        message=f"字段 {ts_field} 有 {failed} 行数据超过 {max_age} 分钟未更新",
        failed_count=failed, total_count=len(non_null), sample_values=sample,
    )


def _enum_check(df: pl.DataFrame, rule: Dict, table: str, total: int) -> Tuple[bool, Optional[RuleViolation]]:
    field = rule.get("field", "")
    allowed = rule.get("allowed_values", [])
    if field not in df.columns:
        return True, None
    if not allowed:
        return True, None
    non_null = df.filter(pl.col(field).is_not_null())
    if len(non_null) == 0:
        return True, None
    mask = non_null[field].cast(pl.Utf8).is_in(allowed)
    failed = (~mask).sum()
    if failed == 0:
        return True, None
    sample = non_null.filter(~mask)[field].head(5).to_list()
    return False, RuleViolation(
        rule_id=rule.get("id"), rule_type="enum_check", table=table, field=field,
        severity=rule.get("severity", "warning"),
        message=f"字段 {field} 有 {failed} 个值不在允许枚举 {allowed} 中",
        failed_count=failed, total_count=len(non_null), sample_values=sample,
    )


# ============================================================
# 4. 主规则引擎类
# ============================================================

class RulesEngine:
    """
    可配置数据质量规则引擎：
    从 rules.yaml 加载规则，对 SQLite 表执行验证，返回违规报告 + 质量评分。
    """

    def __init__(self, db_path: str, config_dir: Optional[Path] = None):
        self.db_path = db_path
        if config_dir is None:
            config_dir = Path(__file__).parent
        self.rules_path = config_dir / "rules.yaml"
        self.config = load_rules_from_yaml(self.rules_path)

    def reload_config(self):
        self.config = load_rules_from_yaml(self.rules_path)

    def _load_table_data(self, table_name: str) -> Optional[pl.DataFrame]:
        conn = sqlite3.connect(self.db_path)
        try:
            df = pl.read_database(f'SELECT * FROM "{table_name}"', connection=conn)
            return df
        except Exception:
            return None
        finally:
            conn.close()

    def validate_table(self, table_name: str) -> TableRuleResult:
        rules = get_rules_for_table(self.config, table_name)
        df = self._load_table_data(table_name)
        if df is None:
            return TableRuleResult(
                table=table_name, total_rules=len(rules), passed=0, failed=len(rules),
                violations=[], quality_score=0.0,
                passed_rules=[], failed_rules=[r.get("id") for r in rules],
            )

        violations = []
        passed_ids = []
        failed_ids = []

        for rule in rules:
            passed, violation = execute_rule(df, rule, table_name)
            if passed:
                passed_ids.append(rule.get("id"))
            else:
                failed_ids.append(rule.get("id"))
                if violation:
                    violations.append(violation)

        total = len(rules)
        score = (len(passed_ids) / max(total, 1)) * 100
        return TableRuleResult(
            table=table_name, total_rules=total, passed=len(passed_ids), failed=len(failed_ids),
            violations=violations, quality_score=score,
            passed_rules=passed_ids, failed_rules=failed_ids,
        )

    def validate_all(self) -> GlobalRuleResult:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        tables = [r[0] for r in cursor.fetchall()]
        conn.close()

        table_results = []
        critical_violations = []
        total_passed = 0
        total_failed = 0
        severity_counts: Dict[str, int] = {"critical": 0, "warning": 0, "info": 0}

        for table in tables:
            result = self.validate_table(table)
            table_results.append(result)
            total_passed += result.passed
            total_failed += result.failed
            for v in result.violations:
                severity_counts[v.severity] = severity_counts.get(v.severity, 0) + 1
                if v.severity == "critical":
                    critical_violations.append(v.to_dict())

        total_rules = total_passed + total_failed
        overall_score = (total_passed / max(total_rules, 1)) * 100

        return GlobalRuleResult(
            total_tables=len(tables), total_rules=total_rules,
            total_passed=total_passed, total_failed=total_failed,
            overall_score=overall_score, table_results=table_results,
            critical_violations=critical_violations[:20],
            summary_by_severity=severity_counts,
        )

    def get_rules(self) -> Dict[str, Any]:
        """返回当前加载的规则配置"""
        return self.config.get("data_quality_rules", {})

    def add_rule(self, table: str, rule: Dict) -> Dict[str, str]:
        """动态添加一条规则（仅内存）"""
        dqr = self.config.setdefault("data_quality_rules", {})
        table_rules = dqr.setdefault("table_rules", {})
        if table not in table_rules:
            table_rules[table] = []
        # 生成 rule_id 如果没有
        if "id" not in rule:
            rule["id"] = f"custom_{uuid.uuid4().hex[:8]}"
        table_rules[table].append(rule)
        return {"status": "added", "rule_id": rule["id"]}

    def remove_rule(self, rule_id: str) -> Dict[str, str]:
        """动态删除一条规则（仅内存）"""
        dqr = self.config.get("data_quality_rules", {})
        table_rules = dqr.get("table_rules", {})
        for tbl, rules in list(table_rules.items()):
            table_rules[tbl] = [r for r in rules if r.get("id") != rule_id]
        defaults = dqr.get("defaults", [])
        dqr["defaults"] = [r for r in defaults if r.get("id") != rule_id]
        return {"status": "removed", "rule_id": rule_id}

    def check_data(self, data: List[Dict], rules: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """
        检查输入数据（字典列表）是否符合规则，返回违规列表。
        用于 POST /api/rules/check 端点。
        """
        if not data:
            return {"violations": [], "total_rows": 0, "quality_score": 100.0}

        df = pl.DataFrame(data)
        violations = []
        rules_to_apply = rules if rules is not None else get_rules_for_table(self.config, "__inline__")

        for rule in rules_to_apply:
            passed, violation = execute_rule(df, rule, "__inline__")
            if not passed and violation:
                violations.append(violation.to_dict())

        total_rows = len(df)
        total_violations = sum(v.get("failed_count", 0) for v in violations)
        quality_score = max(0, 100 - (total_violations / max(total_rows, 1) * 100))

        return {
            "violations": violations,
            "total_rows": total_rows,
            "quality_score": round(quality_score, 1),
        }


# ============================================================
# 入口验证
# ============================================================

if __name__ == "__main__":
    db = str(Path(__file__).parent / "data" / "warehouse.db")
    cfg = Path(__file__).parent
    engine = RulesEngine(db, cfg)
    result = engine.validate_all()
    print(f"\n规则引擎验证结果：总体分数 {result.overall_score:.1f}/100")
    print(f"  表数: {result.total_tables} | 规则: {result.total_rules} | "
          f"通过: {result.total_passed} | 失败: {result.total_failed}")
    for tr in result.table_results:
        if tr.failed > 0:
            print(f"\n  表 {tr.table} — {tr.quality_score:.1f}分 ({tr.passed}/{tr.total_rules} 通过)")
            for v in tr.violations:
                print(f"    [{v.severity}] {v.rule_id} | {v.field}: {v.message}")