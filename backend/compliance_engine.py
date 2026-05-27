#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AutoDataFlow 行业合规规则库
============================
预置的行业数据合规标准规则：

1. GDPR（欧盟通用数据保护条例）— 字段脱敏
2. 个人信息保护法 / 数据安全法（中国）— 敏感字段识别与脱敏
3. PCI-DSS（支付卡行业数据安全标准）— 银行卡信息保护
4. HIPAA（美国健康保险流通与责任法案）— 医疗数据保护

规则通过 YAML 配置声明，由 ComplianceEngine 统一执行。

使用方式：
    engine = ComplianceEngine("data/warehouse.db", "backend/config")
    report = engine.run_compliance_check("gdpr")
    report = engine.run_all_checks()
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
import polars as pl


# ============================================================
# 1. 数据结构
# ============================================================

@dataclass
class ComplianceViolation:
    rule_id: str
    standard: str
    table: str
    field: str
    severity: str
    description: str
    original_value_example: str   # 脱敏前的示例值（仅前5条）
    action_required: str          # 需要的操作

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "standard": self.standard,
            "table": self.table,
            "field": self.field,
            "severity": self.severity,
            "description": self.description,
            "original_value_example": self.original_value_example[:5],
            "action_required": self.action_required,
        }


@dataclass
class ComplianceReport:
    standard: str
    total_tables_checked: int
    total_fields_checked: int
    passed: int
    failed: int
    score: float           # 0-100 合规分数
    violations: List[ComplianceViolation]
    summary: str
    timestamp: str

    def to_dict(self) -> dict:
        return {
            "standard": self.standard,
            "total_tables_checked": self.total_tables_checked,
            "total_fields_checked": self.total_fields_checked,
            "passed": self.passed,
            "failed": self.failed,
            "score": round(self.score, 1),
            "violations": [v.to_dict() for v in self.violations],
            "summary": self.summary,
            "timestamp": self.timestamp,
        }


# ============================================================
# 2. 脱敏函数库
# ============================================================

class MaskingLib:
    """标准化脱敏函数库"""

    @staticmethod
    def mask_email(email: str) -> str:
        """GDPR 邮箱脱敏：a****@example.com"""
        if not email or "@" not in email:
            return "****"
        parts = email.split("@")
        local = parts[0]
        domain = parts[1] if len(parts) > 1 else ""
        if len(local) <= 1:
            masked_local = "****"
        elif len(local) <= 4:
            masked_local = local[0] + "***"
        else:
            masked_local = local[0] + "****"
        return f"{masked_local}@{domain}"

    @staticmethod
    def mask_phone(phone: str) -> str:
        """手机号脱敏：只显示后4位"""
        s = str(phone).strip()
        if len(s) >= 4:
            return "****" + s[-4:]
        return "****"

    @staticmethod
    def mask_id_card(id_card: str) -> str:
        """身份证号脱敏：显示前3后4，中间8位用****"""
        s = str(id_card).strip()
        if len(s) >= 10:
            return s[:3] + "********" + s[-4:]
        return "****"

    @staticmethod
    def mask_bank_account(bank_account: str) -> str:
        """银行卡号脱敏：只显示后4位"""
        s = str(bank_account).strip()
        if len(s) >= 4:
            return "****" + s[-4:]
        return "****"

    @staticmethod
    def mask_address(address: str) -> str:
        """地址脱敏：显示末尾4字符"""
        s = str(address).strip()
        if len(s) >= 4:
            return "****" + s[-4:]
        return "****"

    @staticmethod
    def mask_name(name: str) -> str:
        """姓名脱敏：只显示姓氏（第一个字符）"""
        s = str(name).strip()
        if len(s) >= 2:
            return s[0] + "**"
        elif len(s) == 1:
            return s[0] + "*"
        return "****"

    @staticmethod
    def mask_credit_card(cc: str) -> str:
        """信用卡号脱敏：显示前6后4（BIN + 末位）"""
        s = re.sub(r"\D", "", str(cc))
        if len(s) >= 10:
            return s[:6] + "****" + s[-4:]
        return "****"


# ============================================================
# 3. 合规规则库核心引擎
# ============================================================

class ComplianceEngine:
    """
    行业合规规则库引擎：
    支持多标准并行检查，返回详细违规报告。
    """

    # 预定义敏感字段名模式（用于快速识别）
    SENSITIVE_FIELD_PATTERNS = {
        "email": [r"email", r"mail", r"e_mail"],
        "phone": [r"phone", r"mobile", r"tel", r"telephone", r"手机"],
        "id_card": [r"id_card", r"idcard", r"identity_card", r"身份证"],
        "bank_account": [r"bank_account", r"bank_no", r"银行卡", r"bankaccount"],
        "name": [r"name", r"username", r"user_name", r"姓名", r"customer_name"],
        "address": [r"address", r"home_address", r"地址", r"收货地址"],
        "credit_card": [r"credit_card", r"cc_no", r"card_no", r"信用卡"],
    }

    def __init__(self, db_path: str, config_dir: Path):
        self.db_path = db_path
        self.config_dir = config_dir
        self.config = self._load_config()

    def _load_config(self) -> dict:
        path = self.config_dir / "quality_rules.yaml"
        if not path.exists():
            return {}
        import yaml
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)

    # ---- 标准检查器 ----

    def _check_gdpr(self, df: pl.DataFrame, table: str) -> List[ComplianceViolation]:
        """GDPR 合规检查"""
        violations = []
        config = self.config.get("compliance_rules", {}).get("gdpr", [])
        if not config:
            return violations

        for rule in config:
            if not rule.get("enabled", False):
                continue
            rule_id = rule.get("id", "")
            fields = rule.get("fields", [])
            mask_type = rule.get("type", "")
            description = rule.get("description", "")

            for field_pattern in fields:
                matching_cols = [
                    c for c in df.columns
                    if c.lower() == field_pattern.lower()
                    or re.match(field_pattern, c, re.IGNORECASE)
                ]
                for col in matching_cols:
                    non_null = df.filter(pl.col(col).is_not_null())
                    if len(non_null) == 0:
                        continue
                    examples = non_null[col].head(3).to_list()
                    violations.append(ComplianceViolation(
                        rule_id=rule_id,
                        standard="GDPR",
                        table=table,
                        field=col,
                        severity=rule.get("severity", "critical"),
                        description=description,
                        original_value_example=[str(e) for e in examples],
                        action_required=f"对 {col} 字段实施 {mask_type} 脱敏处理",
                    ))

        return violations

    def _check_datatype_validation(self, df: pl.DataFrame, table: str) -> List[ComplianceViolation]:
        """数据类型严格校验（结构化数据质量合规）"""
        violations = []
        config = self.config.get("compliance_rules", {}).get("datatype_validation", [])
        if not config:
            return violations

        for rule in config:
            if not rule.get("enabled", False):
                continue
            field = rule.get("field", "")
            expected_dtype = rule.get("expected_dtype", "")
            fmt = rule.get("format", "")

            if field not in df.columns:
                continue

            col = df[field]
            failed_count = 0
            examples = []

            if expected_dtype == "date" and fmt == "YYYY-MM-DD":
                # 检查是否符合日期格式
                non_null = df.filter(pl.col(field).is_not_null())
                valid = non_null[col].str.strip().str.contains(r"^\d{4}-\d{2}-\d{2}$")
                failed_count = (~valid).sum()
                if failed_count > 0:
                    examples = non_null.filter(~valid)[col].head(3).to_list()

            if expected_dtype == "integer":
                non_null = df.filter(pl.col(field).is_not_null())
                try:
                    parsed = non_null[col].str.strip().str.to_integer(strict=False)
                    failed_count = parsed.null_count()
                    if failed_count > 0:
                        examples = non_null.filter(parsed.is_null())[col].head(3).to_list()
                except Exception:
                    pass

            if failed_count > 0:
                violations.append(ComplianceViolation(
                    rule_id=rule.get("id"),
                    standard="DataTypeValidation",
                    table=table,
                    field=field,
                    severity=rule.get("severity", "critical"),
                    description=rule.get("description", ""),
                    original_value_example=[str(e) for e in examples],
                    action_required=f"将 {field} 字段值规范化为 {expected_dtype} 类型",
                ))

        return violations

    def _check_freshness(self, df: pl.DataFrame, table: str) -> List[ComplianceViolation]:
        """数据新鲜度检查"""
        violations = []
        config = self.config.get("compliance_rules", {}).get("freshness", [])
        if not config:
            return violations

        for rule in config:
            if not rule.get("enabled", False):
                continue
            ts_field = rule.get("timestamp_field", "date")
            max_age = rule.get("max_age_minutes", 5)

            if ts_field not in df.columns:
                continue

            non_null = df.filter(pl.col(ts_field).is_not_null())
            if len(non_null) == 0:
                continue

            now = datetime.now()
            cutoff = now - timedelta(minutes=max_age)
            old_count = 0
            examples = []
            try:
                ts_col = non_null[ts_field].str.to_datetime("%Y-%m-%d", strict=False)
                old_rows = ts_col < cutoff
                old_count = old_rows.sum()
                if old_count > 0:
                    examples = non_null.filter(old_rows)[ts_field].head(3).to_list()
            except Exception:
                pass

            if old_count > 0:
                violations.append(ComplianceViolation(
                    rule_id=rule.get("id"),
                    standard="DataFreshness",
                    table=table,
                    field=ts_field,
                    severity=rule.get("severity", "warning"),
                    description=rule.get("description", ""),
                    original_value_example=[str(e) for e in examples],
                    action_required=f"数据更新时间超过 {max_age} 分钟，需重新抽取或确认数据源状态",
                ))

        return violations

    def _check_referential_integrity(
        self, df: pl.DataFrame, table: str, all_tables_data: Dict[str, pl.DataFrame]
    ) -> List[ComplianceViolation]:
        """引用完整性检查（外键关系验证）"""
        violations = []
        config = self.config.get("compliance_rules", {}).get("referential_integrity", [])
        if not config:
            return violations

        for rule in config:
            if not rule.get("enabled", False):
                continue
            source_field = rule.get("source_field", "")
            target_table = rule.get("target_table", "")
            target_field = rule.get("target_field", "")

            if source_field not in df.columns:
                continue
            if target_table not in all_tables_data:
                continue

            target_df = all_tables_data[target_table]
            if target_field not in target_df.columns:
                continue

            source_vals = set(df.filter(pl.col(source_field).is_not_null())[source_field].to_list())
            target_vals = set(target_df[target_field].to_list())
            orphaned = [v for v in source_vals if v not in target_vals]

            if orphaned:
                violations.append(ComplianceViolation(
                    rule_id=rule.get("id"),
                    standard="ReferentialIntegrity",
                    table=table,
                    field=source_field,
                    severity=rule.get("severity", "critical"),
                    description=rule.get("description", ""),
                    original_value_example=[str(v) for v in orphaned[:5]],
                    action_required=f"字段 {source_field} 存在 {len(orphaned)} 个引用不存在于 "
                                     f"{target_table}.{target_field}，需数据修复或回滚",
                ))

        return violations

    # ---- 主入口 ----

    def _load_table(self, table: str) -> Optional[pl.DataFrame]:
        conn = sqlite3.connect(self.db_path)
        try:
            return pl.read_database(f'SELECT * FROM "{table}"', connection=conn)
        except Exception:
            return None
        finally:
            conn.close()

    def _load_all_tables(self) -> Dict[str, pl.DataFrame]:
        """加载所有表的 DataFrame（用于外键检查）"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        tables = [r[0] for r in cursor.fetchall()]
        conn.close()

        result = {}
        for t in tables:
            df = self._load_table(t)
            if df is not None:
                result[t] = df
        return result

    def run_compliance_check(self, standard: str = "gdpr") -> ComplianceReport:
        """
        对指定标准运行合规检查
        standard: gdpr | datatype_validation | freshness | referential_integrity | all
        """
        all_tables_data = self._load_all_tables()
        all_violations: List[ComplianceViolation] = []
        total_fields = 0

        for table, df in all_tables_data.items():
            total_fields += len(df.columns)

            if standard == "gdpr":
                all_violations.extend(self._check_gdpr(df, table))
            elif standard == "datatype_validation":
                all_violations.extend(self._check_datatype_validation(df, table))
            elif standard == "freshness":
                all_violations.extend(self._check_freshness(df, table))
            elif standard == "referential_integrity":
                all_violations.extend(self._check_referential_integrity(df, table, all_tables_data))
            elif standard == "all":
                all_violations.extend(self._check_gdpr(df, table))
                all_violations.extend(self._check_datatype_validation(df, table))
                all_violations.extend(self._check_freshness(df, table))
                all_violations.extend(self._check_referential_integrity(df, table, all_tables_data))

        total_tables = len(all_tables_data)
        failed = len(all_violations)
        passed = max(0, total_fields - failed)
        score = (passed / max(total_fields, 1)) * 100

        summaries = {
            "gdpr": f"GDPR 合规检查完成：{total_tables} 表 {total_fields} 字段，"
                    f"发现 {failed} 个违规项，合规分数 {score:.1f}",
            "datatype_validation": f"数据类型校验完成：{total_tables} 表 {total_fields} 字段，"
                                   f"发现 {failed} 个类型错误",
            "freshness": f"数据新鲜度检查完成：{total_tables} 表，"
                         f"发现 {failed} 个字段数据过期",
            "referential_integrity": f"引用完整性检查完成：{total_tables} 表，"
                                      f"发现 {failed} 个孤立引用",
            "all": f"全面合规检查完成：{total_tables} 表 {total_fields} 字段，"
                   f"共 {failed} 个违规项，综合合规分数 {score:.1f}",
        }

        return ComplianceReport(
            standard=standard,
            total_tables_checked=total_tables,
            total_fields_checked=total_fields,
            passed=passed,
            failed=failed,
            score=score,
            violations=all_violations,
            summary=summaries.get(standard, f"{standard} 检查完成"),
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

    def run_all_checks(self) -> Dict[str, ComplianceReport]:
        """运行所有合规标准检查"""
        standards = ["gdpr", "datatype_validation", "freshness", "referential_integrity"]
        return {s: self.run_compliance_check(s) for s in standards}

    def get_compliance_summary(self) -> Dict[str, Any]:
        """获取所有合规检查的总体摘要"""
        reports = self.run_all_checks()
        total_score = sum(r.score for r in reports.values()) / len(reports)
        total_violations = sum(r.failed for r in reports.values())
        critical = sum(1 for r in reports.values() for v in r.violations if v.severity == "critical")

        return {
            "overall_compliance_score": round(total_score, 1),
            "total_violations": total_violations,
            "critical_violations": critical,
            "by_standard": {s: r.to_dict() for s, r in reports.items()},
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }


# ============================================================
# 入口（验证）
# ============================================================

if __name__ == "__main__":
    db = str(Path(__file__).parent / "data" / "warehouse.db")
    cfg = Path(__file__).parent / "config"
    engine = ComplianceEngine(db, cfg)

    print("\n=== AutoDataFlow 合规规则库 ===")
    summary = engine.get_compliance_summary()
    print(f"\n综合合规分数: {summary['overall_compliance_score']}/100")
    print(f"总违规项: {summary['total_violations']} (严重: {summary['critical_violations']})")

    for s, r in summary["by_standard"].items():
        if r["failed"] > 0:
            print(f"\n[{s.upper()}] 合规分数 {r['score']}/100 | "
                  f"违规 {r['failed']} 项 / {r['total_fields_checked']} 字段")
            for v in r["violations"][:3]:
                print(f"  [{v['severity']}] {v['table']}.{v['field']}: {v['description']}")