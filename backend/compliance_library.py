#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AutoDataFlow 合规规则库 — compliance_library.py
================================================
预构建合规规则：
  - GDPR 字段脱敏（email / phone / ID card / bank account）
  - 数据类型校验（date / integer / float / string 严格验证）
  - 新鲜度检查（timestamp 在 N 小时内）

API:
  GET  /api/compliance/rules — 列出所有可用规则
  POST /api/compliance/apply — 对数据集应用合规规则，返回脱敏后数据 + 违规报告
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import polars as pl


# ============================================================
# 1. 预置合规规则定义
# ============================================================

class ComplianceRule:
    """单个合规规则"""

    def __init__(
        self,
        rule_id: str,
        name: str,
        standard: str,
        description: str,
        fields: List[str],
        action: str,  # mask | validate | check_freshness
        severity: str = "critical",
        enabled: bool = True,
        params: Optional[Dict] = None,
    ):
        self.rule_id = rule_id
        self.name = name
        self.standard = standard
        self.description = description
        self.fields = fields  # 字段名或正则模式列表
        self.action = action
        self.severity = severity
        self.enabled = enabled
        self.params = params or {}

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "standard": self.standard,
            "description": self.description,
            "fields": self.fields,
            "action": self.action,
            "severity": self.severity,
            "enabled": self.enabled,
            "params": self.params,
        }


# ============================================================
# 2. 预置规则库
# ============================================================

class ComplianceRuleLibrary:
    """
    预置合规规则库：
    - GDPR 字段脱敏
    - 数据类型校验
    - 新鲜度检查
    """

    @staticmethod
    def get_gdpr_rules() -> List[ComplianceRule]:
        return [
            ComplianceRule(
                rule_id="gdpr_email_mask",
                name="GDPR Email 脱敏",
                standard="GDPR",
                description="邮箱地址脱敏：显示首字符 + ****@domain",
                fields=["email", "Email", "EMAIL", "user_email", "contact_email"],
                action="mask",
                severity="critical",
                params={"mask_type": "email"},
            ),
            ComplianceRule(
                rule_id="gdpr_phone_mask",
                name="GDPR 手机号脱敏",
                standard="GDPR",
                description="手机号脱敏：只显示后4位，前置 ****",
                fields=["phone", "mobile", "tel", "telephone", "手机号"],
                action="mask",
                severity="critical",
                params={"mask_type": "phone"},
            ),
            ComplianceRule(
                rule_id="gdpr_id_card_mask",
                name="GDPR 身份证号脱敏",
                standard="GDPR",
                description="身份证号脱敏：显示前3后4，中间8位用 ****",
                fields=["id_card", "idcard", "identity_card", "身份证", "证件号"],
                action="mask",
                severity="critical",
                params={"mask_type": "id_card"},
            ),
            ComplianceRule(
                rule_id="gdpr_bank_account_mask",
                name="GDPR 银行卡号脱敏",
                standard="GDPR",
                description="银行卡号脱敏：只显示后4位",
                fields=["bank_account", "bank_account_no", "bank_no", "银行卡号", "账户"],
                action="mask",
                severity="critical",
                params={"mask_type": "bank_account"},
            ),
            ComplianceRule(
                rule_id="gdpr_address_mask",
                name="GDPR 地址脱敏",
                standard="GDPR",
                description="地址脱敏：显示末尾4字符",
                fields=["address", "home_address", "address_detail", "地址", "收货地址"],
                action="mask",
                severity="warning",
                params={"mask_type": "address"},
            ),
            ComplianceRule(
                rule_id="gdpr_name_mask",
                name="GDPR 姓名脱敏",
                standard="GDPR",
                description="姓名脱敏：只显示姓氏（首字符）",
                fields=["name", "customer_name", "user_name", "姓名", "客户姓名"],
                action="mask",
                severity="warning",
                params={"mask_type": "name"},
            ),
        ]

    @staticmethod
    def get_datatype_rules() -> List[ComplianceRule]:
        return [
            ComplianceRule(
                rule_id="dt_date_iso",
                name="日期格式校验（ISO）",
                standard="DataTypeValidation",
                description="date/datetime 字段必须符合 YYYY-MM-DD 格式",
                fields=["date", "created_date", "updated_date", "birth_date"],
                action="validate",
                severity="critical",
                params={"expected_dtype": "date", "format": "YYYY-MM-DD"},
            ),
            ComplianceRule(
                rule_id="dt_email_format",
                name="邮箱格式校验",
                standard="DataTypeValidation",
                description="email 字段必须符合 RFC 5322 邮箱格式",
                fields=["email", "Email", "EMAIL"],
                action="validate",
                severity="critical",
                params={"pattern": r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"},
            ),
            ComplianceRule(
                rule_id="dt_phone_format",
                name="手机号格式校验（中国）",
                standard="DataTypeValidation",
                description="phone 字段必须符合中国手机号格式（1[3-9] 开头的11位）",
                fields=["phone", "mobile", "tel"],
                action="validate",
                severity="warning",
                params={"pattern": r"^1[3-9]\d{9}$"},
            ),
        ]

    @staticmethod
    def get_freshness_rules() -> List[ComplianceRule]:
        return [
            ComplianceRule(
                rule_id="freshness_realtime",
                name="实时数据新鲜度（5分钟）",
                standard="DataFreshness",
                description="数据时间戳必须在最近 5 分钟内",
                fields=["timestamp", "updated_at", "created_at", "sync_time"],
                action="check_freshness",
                severity="warning",
                params={"max_age_minutes": 5},
            ),
            ComplianceRule(
                rule_id="freshness_batch",
                name="批量数据新鲜度（24小时）",
                standard="DataFreshness",
                description="数据更新时间必须在 24 小时内",
                fields=["date", "update_date", "batch_date"],
                action="check_freshness",
                severity="info",
                params={"max_age_minutes": 1440},
            ),
        ]

    @staticmethod
    def get_all_rules() -> List[ComplianceRule]:
        rules = []
        rules.extend(ComplianceRuleLibrary.get_gdpr_rules())
        rules.extend(ComplianceRuleLibrary.get_datatype_rules())
        rules.extend(ComplianceRuleLibrary.get_freshness_rules())
        return rules


# ============================================================
# 3. 脱敏函数
# ============================================================

class MaskingFuncs:
    """标准化脱敏函数"""

    @staticmethod
    def mask_email(val: str) -> str:
        if not val or "@" not in str(val):
            return "****"
        parts = str(val).split("@")
        local, domain = parts[0], parts[1] if len(parts) > 1 else ""
        masked = local[0] + "****" if len(local) > 0 else "****"
        return f"{masked}@{domain}"

    @staticmethod
    def mask_phone(val: str) -> str:
        s = str(val).strip()
        return "****" + s[-4:] if len(s) >= 4 else "****"

    @staticmethod
    def mask_id_card(val: str) -> str:
        s = str(val).strip()
        return s[:3] + "********" + s[-4:] if len(s) >= 10 else "****"

    @staticmethod
    def mask_bank_account(val: str) -> str:
        s = str(val).strip()
        return "****" + s[-4:] if len(s) >= 4 else "****"

    @staticmethod
    def mask_address(val: str) -> str:
        s = str(val).strip()
        return "****" + s[-4:] if len(s) >= 4 else "****"

    @staticmethod
    def mask_name(val: str) -> str:
        s = str(val).strip()
        return s[0] + "**" if len(s) >= 2 else s[0] + "*" if len(s) == 1 else "****"


# ============================================================
# 4. 合规引擎（应用规则到数据）
# ============================================================

class ComplianceLibrary:
    """
    合规规则库引擎：
    - 列出所有可用合规规则
    - 对数据集应用规则（脱敏 / 校验 / 新鲜度检查）
    - 返回脱敏后数据 + 违规报告
    """

    def __init__(self, db_path: Optional[str] = None, config_dir: Optional[Path] = None):
        if config_dir is None:
            config_dir = Path(__file__).parent
        if db_path is None:
            db_path = str(config_dir.parent / "data" / "warehouse.db")
        self.db_path = db_path
        self.config_dir = config_dir
        self.library = ComplianceRuleLibrary()

    def list_rules(self, standard: Optional[str] = None) -> List[Dict]:
        """列出所有可用合规规则"""
        all_rules = self.library.get_all_rules()
        if standard:
            all_rules = [r for r in all_rules if r.standard == standard]
        return [r.to_dict() for r in all_rules]

    def apply(
        self,
        data: List[Dict],
        rule_ids: Optional[List[str]] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        对数据集应用合规规则。

        参数：
            data: 输入数据（dict list）
            rule_ids: 要应用的规则 ID 列表（None = 应用全部）
            dry_run: True = 只检查不修改

        返回：
            {
                "success": True,
                "applied_rules": [...],
                "violations": [...],
                "masked_data": [...],   # 脱敏后的数据（dry_run=False 时有）
                "summary": {...}
            }
        """
        if not data:
            return {"success": False, "error": "无数据", "violations": [], "masked_data": None}

        df = pl.DataFrame(data)
        all_rules = self.library.get_all_rules()

        # 按 rule_ids 过滤
        if rule_ids:
            selected = [r for r in all_rules if r.rule_id in rule_ids and r.enabled]
        else:
            selected = [r for r in all_rules if r.enabled]

        violations = []
        masked_data = None

        for rule in selected:
            for field in rule.fields:
                # 找匹配的列
                matching_cols = [
                    c for c in df.columns
                    if c.lower() == field.lower() or re.match(field, c, re.IGNORECASE)
                ]
                for col in matching_cols:
                    if rule.action == "mask":
                        violations.extend(
                            self._apply_mask(df, rule, col, dry_run)
                        )
                    elif rule.action == "validate":
                        violations.extend(
                            self._apply_validate(df, rule, col)
                        )
                    elif rule.action == "check_freshness":
                        violations.extend(
                            self._apply_freshness(df, rule, col)
                        )

        # 脱敏数据（仅在非 dry_run 时生成）
        if not dry_run:
            df_masked = df.clone()
            for v in violations:
                col = v["field"]
                rule_id = v["rule_id"]
                # 找到对应规则
                rule = next((r for r in selected if r.rule_id == rule_id), None)
                if rule and rule.action == "mask" and col in df_masked.columns:
                    df_masked = self._do_mask(df_masked, col, rule.params.get("mask_type", ""))

            masked_data = df_masked.to_dicts()

        # 生成摘要
        severity_counts = {"critical": 0, "warning": 0, "info": 0}
        for v in violations:
            severity_counts[v["severity"]] = severity_counts.get(v["severity"], 0) + 1

        return {
            "success": True,
            "applied_rules": [r.rule_id for r in selected],
            "violations": violations,
            "masked_data": masked_data,
            "summary": {
                "total_violations": len(violations),
                "critical": severity_counts["critical"],
                "warning": severity_counts["warning"],
                "info": severity_counts["info"],
                "records_checked": len(df),
                "fields_checked": len(df.columns),
            },
        }

    def _apply_mask(
        self, df: pl.DataFrame, rule: ComplianceRule, col: str, dry_run: bool
    ) -> List[Dict]:
        """应用脱敏规则"""
        violations = []
        non_null = df.filter(pl.col(col).is_not_null())
        if len(non_null) == 0:
            return violations

        mask_type = rule.params.get("mask_type", "")
        examples = non_null[col].head(3).to_list()

        violations.append({
            "rule_id": rule.rule_id,
            "rule_name": rule.name,
            "standard": rule.standard,
            "field": col,
            "severity": rule.severity,
            "action": "mask",
            "description": f"字段 {col} 需要 {mask_type} 脱敏处理",
            "affected_rows": len(non_null),
            "example_values": [str(e) for e in examples],
        })
        return violations

    def _apply_validate(
        self, df: pl.DataFrame, rule: ComplianceRule, col: str
    ) -> List[Dict]:
        """应用数据类型校验规则"""
        violations = []
        non_null = df.filter(pl.col(col).is_not_null())
        if len(non_null) == 0:
            return violations

        expected_dtype = rule.params.get("expected_dtype", "")
        pattern = rule.params.get("pattern", "")
        failed = 0
        examples = []

        if expected_dtype == "date":
            valid_mask = non_null[col].str.contains(r"^\d{4}-\d{2}-\d{2}$")
            failed = (~valid_mask).sum()
            if failed > 0:
                examples = non_null.filter(~valid_mask)[col].head(3).to_list()
        elif pattern:
            try:
                regex = re.compile(pattern)
                valid_mask = non_null[col].cast(pl.Utf8).map_elements(
                    lambda v: bool(regex.match(str(v))), return_dtype=pl.Boolean
                )
                failed = (~valid_mask).sum()
                if failed > 0:
                    examples = non_null.filter(~valid_mask)[col].head(3).to_list()
            except Exception:
                pass

        if failed > 0:
            violations.append({
                "rule_id": rule.rule_id,
                "rule_name": rule.name,
                "standard": rule.standard,
                "field": col,
                "severity": rule.severity,
                "action": "validate",
                "description": f"字段 {col} 有 {failed} 个值不符合格式要求",
                "affected_rows": int(failed),
                "example_values": [str(e) for e in examples],
            })

        return violations

    def _apply_freshness(
        self, df: pl.DataFrame, rule: ComplianceRule, col: str
    ) -> List[Dict]:
        """应用新鲜度检查规则"""
        violations = []
        non_null = df.filter(pl.col(col).is_not_null())
        if len(non_null) == 0:
            return violations

        max_age = rule.params.get("max_age_minutes", 60)
        cutoff = datetime.now() - timedelta(minutes=max_age)

        try:
            ts_col = non_null[col].str.to_datetime("%Y-%m-%d", strict=False)
            old_rows = ts_col < cutoff
            failed = old_rows.sum()
            if failed > 0:
                examples = non_null.filter(old_rows)[col].head(3).to_list()
                violations.append({
                    "rule_id": rule.rule_id,
                    "rule_name": rule.name,
                    "standard": rule.standard,
                    "field": col,
                    "severity": rule.severity,
                    "action": "check_freshness",
                    "description": f"字段 {col} 有 {failed} 行数据超过 {max_age} 分钟未更新",
                    "affected_rows": int(failed),
                    "example_values": [str(e) for e in examples],
                })
        except Exception:
            pass

        return violations

    def _do_mask(
        self, df: pl.DataFrame, col: str, mask_type: str
    ) -> pl.DataFrame:
        """执行脱敏操作"""
        mask_fn_map = {
            "email": MaskingFuncs.mask_email,
            "phone": MaskingFuncs.mask_phone,
            "id_card": MaskingFuncs.mask_id_card,
            "bank_account": MaskingFuncs.mask_bank_account,
            "address": MaskingFuncs.mask_address,
            "name": MaskingFuncs.mask_name,
        }
        fn = mask_fn_map.get(mask_type)
        if fn is None:
            return df

        non_null_mask = pl.col(col).is_not_null()
        df = df.with_columns(
            pl.when(non_null_mask)
            .then(pl.col(col).map_elements(fn, return_dtype=pl.Utf8))
            .otherwise(pl.col(col))
            .alias(col)
        )
        return df


# ============================================================
# 入口验证
# ============================================================

if __name__ == "__main__":
    # 测试
    sample_data = [
        {"id": 1, "name": "Alice Wang", "email": "alice@example.com", "phone": "13812345678", "id_card": "110101199001011234", "amount": 150.5, "date": "2026-05-18"},
        {"id": 2, "name": "Bob Li", "email": "bobatexample.com", "phone": "1391234567", "id_card": "110101199002022345", "amount": 200, "date": "2026-04-01"},
        {"id": 3, "name": "Charlie Chen", "email": "charlie@test.com", "phone": "13712345678", "id_card": "110101199003033456", "amount": 300, "date": "2026-05-15"},
    ]

    lib = ComplianceLibrary()
    print("\n=== 合规规则库 ===")
    rules = lib.list_rules()
    print(f"可用规则数: {len(rules)}")
    for r in rules:
        print(f"  [{r['standard']}] {r['rule_id']} - {r['name']}")

    print("\n=== 应用 GDPR 脱敏 ===")
    result = lib.apply(sample_data)
    print(f"违规数: {result['summary']['total_violations']}")
    print(f"严重: {result['summary']['critical']} | 警告: {result['summary']['warning']}")

    if result["masked_data"]:
        print("\n脱敏后数据:")
        for row in result["masked_data"]:
            print(f"  {row['name']} | {row['email']} | {row['phone']} | {row['id_card']}")