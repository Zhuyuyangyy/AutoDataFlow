#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AutoDataFlow 可配置数据质量规则引擎
====================================
支持用户自定义规则（YAML 声明式），运行时用 Polars 执行验证。
规则类型：null_check / regex_match / range_check / uniqueness /
          datatype_check / length_check / freshness_check / enum_check
"""

from __future__ import annotations

import re
import sqlite3
import json
import yaml
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
import polars as pl


# ============================================================
# 1. 数据结构
# ============================================================

class RuleType(str, Enum):
    NULL_CHECK       = "null_check"
    REGEX_MATCH      = "regex_match"
    RANGE_CHECK      = "range_check"
    UNIQUENESS       = "uniqueness"
    DATATYPE_CHECK   = "datatype_check"
    LENGTH_CHECK     = "length_check"
    FRESHNESS_CHECK  = "freshness_check"
    ENUM_CHECK       = "enum_check"


class Severity(str, Enum):
    CRITICAL = "critical"
    WARNING  = "warning"
    INFO     = "info"


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
    quality_score: float  # 0-100 based on rule pass rate
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
# 2. 规则加载器
# ============================================================

class RuleLoader:
    """从 YAML 加载质量规则和合规规则"""

    @staticmethod
    def load_rules(config_path: Path) -> Dict[str, Any]:
        if not config_path.exists():
            return {"data_quality_rules": {"defaults": [], "table_rules": {}}}
        with open(config_path, encoding="utf-8") as f:
            return yaml.safe_load(f)

    @staticmethod
    def get_rules_for_table(config: Dict[str, Any], table_name: str) -> List[Dict]:
        """获取某个表适用的所有规则（defaults + 表级 + 通配符）"""
        dqr = config.get("data_quality_rules", {})
        defaults = dqr.get("defaults", [])
        table_rules = dqr.get("table_rules", {})

        # 收集该表规则（精确匹配优先，然后通配符）
        rules = []
        # 通配规则
        wildcard = table_rules.get("*", [])
        rules.extend([{**r, "applies_to": "*"} for r in wildcard if r.get("enabled", True)])
        # 精确表名规则
        specific = table_rules.get(table_name, [])
        rules.extend([{**r, "applies_to": table_name} for r in specific if r.get("enabled", True)])
        # 默认规则
        rules.extend([{**r, "applies_to": "defaults"} for r in defaults if r.get("enabled", True)])

        return rules


# ============================================================
# 3. Polars 规则执行器
# ============================================================

class RuleExecutor:
    """用 Polars 对 DataFrame 执行单条规则"""

    @staticmethod
    def execute(df: pl.DataFrame, rule: Dict, table: str) -> Tuple[bool, RuleViolation]:
        """
        执行一条规则，返回 (passed, violation_or_None)
        """
        rule_id = rule.get("id", "unknown")
        rule_type = rule.get("type", "")
        field = rule.get("field", "")
        severity = rule.get("severity", "warning")

        # 如果规则没有指定 field 且不是全局规则，跳过
        if not field and rule_type not in ("uniqueness",):
            return True, None

        total_count = len(df)

        try:
            if rule_type == RuleType.NULL_CHECK:
                return RuleExecutor._null_check(df, rule, table, total_count)
            elif rule_type == RuleType.REGEX_MATCH:
                return RuleExecutor._regex_match(df, rule, table, total_count)
            elif rule_type == RuleType.RANGE_CHECK:
                return RuleExecutor._range_check(df, rule, table, total_count)
            elif rule_type == RuleType.UNIQUENESS:
                return RuleExecutor._uniqueness(df, rule, table, total_count)
            elif rule_type == RuleType.DATATYPE_CHECK:
                return RuleExecutor._datatype_check(df, rule, table, total_count)
            elif rule_type == RuleType.LENGTH_CHECK:
                return RuleExecutor._length_check(df, rule, table, total_count)
            elif rule_type == RuleType.FRESHNESS_CHECK:
                return RuleExecutor._freshness_check(df, rule, table, total_count)
            elif rule_type == RuleType.ENUM_CHECK:
                return RuleExecutor._enum_check(df, rule, table, total_count)
            else:
                return True, None
        except Exception as e:
            violation = RuleViolation(
                rule_id=rule_id,
                rule_type=rule_type,
                table=table,
                field=field,
                severity=severity,
                message=f"规则执行异常: {e}",
                failed_count=0,
                total_count=total_count,
            )
            return False, violation

    # ---- 各类规则实现 ----

    @staticmethod
    def _null_check(df: pl.DataFrame, rule: Dict, table: str, total: int) -> Tuple[bool, Optional[RuleViolation]]:
        """字段必须非空"""
        field = rule.get("field", "")
        if field not in df.columns:
            return True, None
        null_count = df[field].null_count()
        failed = null_count
        if failed == 0:
            return True, None
        sample = df.filter(pl.col(field).is_null())[field].head(5).to_list()
        return False, RuleViolation(
            rule_id=rule.get("id"),
            rule_type="null_check",
            table=table,
            field=field,
            severity=rule.get("severity", "critical"),
            message=f"字段 {field} 存在 {failed}/{total} 个空值",
            failed_count=failed,
            total_count=total,
            sample_values=sample,
        )

    @staticmethod
    def _regex_match(df: pl.DataFrame, rule: Dict, table: str, total: int) -> Tuple[bool, Optional[RuleViolation]]:
        """字段值必须匹配正则"""
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
            rule_id=rule.get("id"),
            rule_type="regex_match",
            table=table,
            field=field,
            severity=rule.get("severity", "critical"),
            message=f"字段 {field} 有 {failed} 个值不匹配正则: {pattern}",
            failed_count=failed,
            total_count=len(non_null),
            sample_values=sample,
        )

    @staticmethod
    def _range_check(df: pl.DataFrame, rule: Dict, table: str, total: int) -> Tuple[bool, Optional[RuleViolation]]:
        """数值必须在 [min, max]"""
        field = rule.get("field", "")
        min_val = rule.get("min")
        max_val = rule.get("max")
        if field not in df.columns:
            return True, None
        numeric = df.select(pl.col(field)).to_series()
        if not numeric.dtype in (pl.Int64, pl.Int32, pl.Float64, pl.Float32):
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
            rule_id=rule.get("id"),
            rule_type="range_check",
            table=table,
            field=field,
            severity=rule.get("severity", "critical"),
            message=f"字段 {field} 有 {failed} 个值超出范围 [{min_val}, {max_val}]",
            failed_count=failed,
            total_count=len(non_null),
            sample_values=sample,
        )

    @staticmethod
    def _uniqueness(df: pl.DataFrame, rule: Dict, table: str, total: int) -> Tuple[bool, Optional[RuleViolation]]:
        """字段值必须唯一（无重复）"""
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
        dup_values = non_null.filter(dup_mask)[field].head(5).to_list()
        return False, RuleViolation(
            rule_id=rule.get("id"),
            rule_type="uniqueness",
            table=table,
            field=field,
            severity=rule.get("severity", "critical"),
            message=f"字段 {field} 存在 {failed} 个重复值",
            failed_count=failed,
            total_count=len(non_null),
            sample_values=dup_values,
        )

    @staticmethod
    def _datatype_check(df: pl.DataFrame, rule: Dict, table: str, total: int) -> Tuple[bool, Optional[RuleViolation]]:
        """数据类型校验"""
        field = rule.get("field", "")
        expected_dtype = rule.get("expected_dtype", "")
        fmt = rule.get("format", "")
        if field not in df.columns:
            return True, None
        col = df[field]
        dtype_map = {
            "integer": [pl.Int64, pl.Int32],
            "float": [pl.Float64, pl.Float32],
            "string": [pl.Utf8],
            "date": [pl.Date],
            "datetime": [pl.Datetime],
            "boolean": [pl.Boolean],
        }
        expected_types = dtype_map.get(expected_dtype, [])
        if not expected_types:
            return True, None
        if col.dtype not in expected_types:
            # 尝试转换
            sample = col.cast(expected_types[0], strict=False).null_count()
            failed = sample
        else:
            failed = 0
        if failed == 0:
            return True, None
        return False, RuleViolation(
            rule_id=rule.get("id"),
            rule_type="datatype_check",
            table=table,
            field=field,
            severity=rule.get("severity", "critical"),
            message=f"字段 {field} 类型为 {col.dtype}，不符合预期类型 {expected_dtype}",
            failed_count=failed,
            total_count=total,
        )

    @staticmethod
    def _length_check(df: pl.DataFrame, rule: Dict, table: str, total: int) -> Tuple[bool, Optional[RuleViolation]]:
        """字符串长度检查"""
        field = rule.get("field", "")
        min_len = rule.get("min_len", 0)
        max_len = rule.get("max_len", 9999)
        if field not in df.columns:
            return True, None
        non_null = df.filter(pl.col(field).is_not_null())
        if len(non_null) == 0:
            return True, None
        str_col = non_null[field].cast(pl.Utf8)
        mask = str_col.str.len_chars() >= min_len
        mask = mask & (str_col.str.len_chars() <= max_len)
        failed = (~mask).sum()
        if failed == 0:
            return True, None
        sample = non_null.filter(~mask)[field].head(5).to_list()
        return False, RuleViolation(
            rule_id=rule.get("id"),
            rule_type="length_check",
            table=table,
            field=field,
            severity=rule.get("severity", "critical"),
            message=f"字段 {field} 有 {failed} 个值长度超出 [{min_len}, {max_len}]",
            failed_count=failed,
            total_count=len(non_null),
            sample_values=sample,
        )

    @staticmethod
    def _freshness_check(df: pl.DataFrame, rule: Dict, table: str, total: int) -> Tuple[bool, Optional[RuleViolation]]:
        """数据新鲜度检查"""
        ts_field = rule.get("timestamp_field", "date")
        max_age = rule.get("max_age_minutes", 5)
        if ts_field not in df.columns:
            return True, None
        non_null = df.filter(pl.col(ts_field).is_not_null())
        if len(non_null) == 0:
            return True, None
        now = datetime.now()
        cutoff = now - timedelta(minutes=max_age)
        # 尝试解析日期
        try:
            ts_col = non_null[ts_field].str.to_datetime("%Y-%m-%d %H:%M:%S", strict=False)
        except Exception:
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
            rule_id=rule.get("id"),
            rule_type="freshness_check",
            table=table,
            field=ts_field,
            severity=rule.get("severity", "warning"),
            message=f"字段 {ts_field} 有 {failed} 行数据超过 {max_age} 分钟未更新",
            failed_count=failed,
            total_count=len(non_null),
            sample_values=sample,
        )

    @staticmethod
    def _enum_check(df: pl.DataFrame, rule: Dict, table: str, total: int) -> Tuple[bool, Optional[RuleViolation]]:
        """枚举值检查"""
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
            rule_id=rule.get("id"),
            rule_type="enum_check",
            table=table,
            field=field,
            severity=rule.get("severity", "warning"),
            message=f"字段 {field} 有 {failed} 个值不在允许枚举 {allowed} 中",
            failed_count=failed,
            total_count=len(non_null),
            sample_values=sample,
        )


# ============================================================
# 4. 主规则引擎
# ============================================================

class DataQualityRuleEngine:
    """
    可配置数据质量规则引擎：
    1. 从 YAML 加载用户自定义规则
    2. 对 SQLite 表执行规则验证
    3. 返回详细违规报告 + 质量评分
    """

    def __init__(self, db_path: str, config_dir: Path):
        self.db_path = db_path
        self.config_path = config_dir / "quality_rules.yaml"
        self.config = RuleLoader.load_rules(self.config_path)

    def reload_config(self):
        self.config = RuleLoader.load_rules(self.config_path)

    def _load_table_data(self, table_name: str) -> Optional[pl.DataFrame]:
        """从 SQLite 加载表数据为 Polars DataFrame"""
        conn = sqlite3.connect(self.db_path)
        try:
            query = f'SELECT * FROM "{table_name}"'
            df = pl.read_database(query, connection=conn)
            return df
        except Exception:
            return None
        finally:
            conn.close()

    def validate_table(self, table_name: str) -> TableRuleResult:
        """验证单表的全部规则"""
        rules = RuleLoader.get_rules_for_table(self.config, table_name)
        df = self._load_table_data(table_name)
        if df is None:
            return TableRuleResult(
                table=table_name,
                total_rules=len(rules),
                passed=0, failed=len(rules),
                violations=[],
                quality_score=0,
                passed_rules=[],
                failed_rules=[r.get("id") for r in rules],
            )

        violations = []
        passed_ids = []
        failed_ids = []

        for rule in rules:
            passed, violation = RuleExecutor.execute(df, rule, table_name)
            if passed:
                passed_ids.append(rule.get("id"))
            else:
                failed_ids.append(rule.get("id"))
                if violation:
                    violations.append(violation)

        total = len(rules)
        passed_count = len(passed_ids)
        failed_count = len(failed_ids)
        score = (passed_count / max(total, 1)) * 100

        return TableRuleResult(
            table=table_name,
            total_rules=total,
            passed=passed_count,
            failed=failed_count,
            violations=violations,
            quality_score=score,
            passed_rules=passed_ids,
            failed_rules=failed_ids,
        )

    def validate_all(self) -> GlobalRuleResult:
        """验证所有表的规则"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        tables = [row[0] for row in cursor.fetchall()]
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
            total_tables=len(tables),
            total_rules=total_rules,
            total_passed=total_passed,
            total_failed=total_failed,
            overall_score=overall_score,
            table_results=table_results,
            critical_violations=critical_violations[:20],
            summary_by_severity=severity_counts,
        )

    def get_custom_rules(self) -> Dict[str, Any]:
        """返回当前加载的规则配置（供管理 API 使用）"""
        return self.config.get("data_quality_rules", {})

    def add_custom_rule(self, table: str, rule: Dict) -> Dict[str, str]:
        """动态添加一条自定义规则（仅内存，下次启动需写入 YAML）"""
        dqr = self.config.setdefault("data_quality_rules", {})
        table_rules = dqr.setdefault("table_rules", {})
        if table not in table_rules:
            table_rules[table] = []
        table_rules[table].append(rule)
        return {"status": "added", "rule_id": rule.get("id")}

    def remove_custom_rule(self, rule_id: str) -> Dict[str, str]:
        """动态删除一条自定义规则（仅内存）"""
        dqr = self.config.get("data_quality_rules", {})
        table_rules = dqr.get("table_rules", {})
        for tbl, rules in table_rules.items():
            table_rules[tbl] = [r for r in rules if r.get("id") != rule_id]
        defaults = dqr.get("defaults", [])
        dqr["defaults"] = [r for r in defaults if r.get("id") != rule_id]
        return {"status": "removed", "rule_id": rule_id}


# ============================================================
# 入口（验证）
# ============================================================

if __name__ == "__main__":
    db = str(Path(__file__).parent / "data" / "warehouse.db")
    cfg = Path(__file__).parent / "config"
    engine = DataQualityRuleEngine(db, cfg)
    result = engine.validate_all()
    print(f"\n规则引擎验证结果：总体分数 {result.overall_score:.1f}/100")
    print(f"  表数: {result.total_tables} | 规则: {result.total_rules} | "
          f"通过: {result.total_passed} | 失败: {result.total_failed}")
    for tr in result.table_results:
        if tr.failed > 0:
            print(f"\n  表 {tr.table} — {tr.quality_score:.1f}分 ({tr.passed}/{tr.total_rules} 通过)")
            for v in tr.violations:
                print(f"    [{v.severity}] {v.rule_id} | {v.field}: {v.message}")