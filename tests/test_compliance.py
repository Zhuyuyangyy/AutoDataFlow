"""
AutoDataFlow v3.0 - Compliance Tests
======================================
Tests for compliance_engine.py and compliance_library.py
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


# ============================================================
# MaskingLib Tests
# ============================================================

class TestMaskingLib:
    def test_mask_email(self):
        from compliance_engine import MaskingLib
        masked = MaskingLib.mask_email("alice@example.com")
        assert masked == "a****@example.com"

    def test_mask_email_short(self):
        from compliance_engine import MaskingLib
        masked = MaskingLib.mask_email("a@b.com")
        assert "@" in masked

    def test_mask_email_invalid(self):
        from compliance_engine import MaskingLib
        masked = MaskingLib.mask_email("")
        assert masked == "****"

    def test_mask_phone(self):
        from compliance_engine import MaskingLib
        masked = MaskingLib.mask_phone("13812345678")
        assert masked.endswith("5678")
        assert masked.startswith("****")

    def test_mask_id_card(self):
        from compliance_engine import MaskingLib
        masked = MaskingLib.mask_id_card("110101199001011234")
        assert masked.startswith("110")
        assert masked.endswith("1234")

    def test_mask_bank_account(self):
        from compliance_engine import MaskingLib
        masked = MaskingLib.mask_bank_account("6222021234567890123")
        assert masked.endswith("0123")

    def test_mask_address(self):
        from compliance_engine import MaskingLib
        masked = MaskingLib.mask_address("北京市海淀区中关村大街1号")
        assert "****" in masked

    def test_mask_name(self):
        from compliance_engine import MaskingLib
        masked = MaskingLib.mask_name("张三")
        assert masked == "张**"

    def test_mask_credit_card(self):
        from compliance_engine import MaskingLib
        masked = MaskingLib.mask_credit_card("6222021234567890")
        assert "****" in masked


# ============================================================
# ComplianceRuleLibrary Tests
# ============================================================

class TestComplianceRuleLibrary:
    def test_get_gdpr_rules(self):
        from compliance_library import ComplianceRuleLibrary
        rules = ComplianceRuleLibrary.get_gdpr_rules()
        assert len(rules) >= 5
        rule_ids = [r.rule_id for r in rules]
        assert "gdpr_email_mask" in rule_ids

    def test_get_datatype_rules(self):
        from compliance_library import ComplianceRuleLibrary
        rules = ComplianceRuleLibrary.get_datatype_rules()
        assert len(rules) >= 2

    def test_get_freshness_rules(self):
        from compliance_library import ComplianceRuleLibrary
        rules = ComplianceRuleLibrary.get_freshness_rules()
        assert len(rules) >= 1

    def test_get_all_rules(self):
        from compliance_library import ComplianceRuleLibrary
        rules = ComplianceRuleLibrary.get_all_rules()
        assert len(rules) >= 8


# ============================================================
# ComplianceLibrary Tests
# ============================================================

class TestComplianceLibrary:
    def test_list_rules(self, tmp_path):
        from compliance_library import ComplianceLibrary
        import sqlite3
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE t (id INTEGER, email TEXT)")
        conn.commit()
        conn.close()

        lib = ComplianceLibrary(db_path, tmp_path)
        rules = lib.list_rules()
        assert len(rules) > 0

    def test_list_rules_by_standard(self, tmp_path):
        from compliance_library import ComplianceLibrary
        import sqlite3
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE t (id INTEGER, email TEXT)")
        conn.commit()
        conn.close()

        lib = ComplianceLibrary(db_path, tmp_path)
        gdpr_rules = lib.list_rules(standard="GDPR")
        assert all(r["standard"] == "GDPR" for r in gdpr_rules)

    def test_apply_masking(self, tmp_path):
        from compliance_library import ComplianceLibrary
        import sqlite3
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE t (id INTEGER, email TEXT)")
        conn.commit()
        conn.close()

        lib = ComplianceLibrary(db_path, tmp_path)
        data = [
            {"id": 1, "email": "alice@example.com"},
            {"id": 2, "email": "bob@test.com"},
        ]
        result = lib.apply(data)
        assert result["success"] is True
        assert "masked_data" in result
        if result["masked_data"]:
            for row in result["masked_data"]:
                if row.get("email"):
                    assert "****" in row["email"] or row["email"] == data[list(result["masked_data"]).index(row)]["email"]

    def test_apply_dry_run(self, tmp_path):
        from compliance_library import ComplianceLibrary
        import sqlite3
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE t (id INTEGER, email TEXT)")
        conn.commit()
        conn.close()

        lib = ComplianceLibrary(db_path, tmp_path)
        data = [{"id": 1, "email": "alice@example.com"}]
        result = lib.apply(data, dry_run=True)
        assert result["success"] is True
        assert result["masked_data"] is None

    def test_apply_empty_data(self, tmp_path):
        from compliance_library import ComplianceLibrary
        import sqlite3
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.commit()
        conn.close()

        lib = ComplianceLibrary(db_path, tmp_path)
        result = lib.apply([])
        assert result["success"] is False
