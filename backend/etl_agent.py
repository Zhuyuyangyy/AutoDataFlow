#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AutoDataFlow ETL 增强 — etl_agent.py
=====================================
基于 Polars 的 ETL 清洗 API：接受 CSV/JSON 输入，应用 rules.yaml 中的规则，
返回清洗后的数据 + 质量评分。

API:
  POST /api/etl/clean — 接受 JSON/CSV 数据，应用规则，返回清洗结果
  GET  /api/etl/clean — 同上但通过 query params（少量数据）

质量评分 = 100 - (violations / total_rows * 100)
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import polars as pl

from rules_engine import RulesEngine, get_rules_for_table


# ============================================================
# 1. 解析器（CSV / JSON → Polars DataFrame）
# ============================================================

def parse_input(
    payload: Union[str, bytes, Dict, List],
    content_type: Optional[str] = None,
) -> pl.DataFrame:
    """
    将各种格式的输入解析为 Polars DataFrame。
    支持：JSON (list or object), CSV,Dict list
    """
    # 如果已经是 dict/list（FastAPI Pydantic 模型），直接转
    if isinstance(payload, (dict, list)):
        if isinstance(payload, dict):
            # 单条记录包装为 list
            payload = [payload]
        return pl.DataFrame(payload)

    # 字符串/字节流
    text = payload
    if isinstance(payload, bytes):
        text = payload.decode("utf-8", errors="replace")

    # 尝试 JSON
    text_stripped = text.strip()
    if text_stripped.startswith("[") or text_stripped.startswith("{"):
        try:
            data = json.loads(text_stripped)
            if isinstance(data, dict):
                data = [data]
            return pl.DataFrame(data)
        except Exception:
            pass

    # CSV
    try:
        lines = text_stripped.split("\n")
        if len(lines) > 1:
            reader = csv.DictReader(io.StringIO(text_stripped))
            rows = list(reader)
            if rows:
                return pl.DataFrame(rows)
    except Exception:
        pass

    raise ValueError("无法解析输入数据，请提供 JSON 数组或 CSV 格式")


def serialize_output(df: pl.DataFrame, format: str = "json") -> Union[str, Dict, List]:
    """
    将清洗后的 DataFrame 序列化为指定格式。
    format: json | csv | records
    """
    if format == "csv":
        return df.write_csv()
    elif format == "records":
        return df.to_dicts()
    else:  # json
        return df.to_json()


# ============================================================
# 2. 质量评分
# ============================================================

def compute_quality_score(df: pl.DataFrame, violations: List[Dict]) -> float:
    """质量评分 = 100 - (violations / total_rows * 100)"""
    if len(df) == 0:
        return 100.0
    total_violations = sum(v.get("failed_count", 0) for v in violations)
    return max(0.0, 100.0 - (total_violations / len(df) * 100))


# ============================================================
# 3. 清洗主逻辑
# ============================================================

def clean_data(
    df: pl.DataFrame,
    rules: List[Dict],
    apply_fixes: bool = False,
) -> Dict[str, Any]:
    """
    对 DataFrame 应用规则清洗。

    参数：
        df: 输入数据（Polars DataFrame）
        rules: 从 rules.yaml 加载的规则列表
        apply_fixes: 是否自动修复（drop_nulls / clip_outliers）

    返回：
        {
            "cleaned_data": [...],
            "violations": [...],
            "quality_score": 85.5,
            "stats": { "total_rows": 1000, "removed_rows": 5, "modified_cells": 12 }
        }
    """
    violations = []
    stats = {
        "total_rows": len(df),
        "removed_rows": 0,
        "modified_cells": 0,
    }

    # 执行规则检查
    for rule in rules:
        rule_id = rule.get("id", "unknown")
        rule_type = rule.get("type", "")
        field = rule.get("field", "")

        if not field and rule_type not in ("uniqueness",):
            continue

        if field and field not in df.columns:
            continue

        if rule_type == "null_check":
            null_count = df[field].null_count() if field in df.columns else 0
            if null_count > 0:
                violations.append({
                    "rule_id": rule_id, "rule_type": rule_type,
                    "field": field, "severity": rule.get("severity", "warning"),
                    "message": f"字段 {field} 有 {null_count} 个空值",
                    "failed_count": null_count, "total_count": len(df),
                })
                if apply_fixes:
                    df = df.filter(pl.col(field).is_not_null())
                    stats["removed_rows"] += null_count

        elif rule_type == "regex_match":
            pattern = rule.get("pattern", "")
            if field in df.columns:
                non_null = df.filter(pl.col(field).is_not_null())
                try:
                    regex = __import__("re").compile(pattern)
                    mask = non_null[field].cast(pl.Utf8).map_elements(
                        lambda v: bool(regex.match(str(v))), return_dtype=pl.Boolean
                    )
                    failed = (~mask).sum()
                    if failed > 0:
                        violations.append({
                            "rule_id": rule_id, "rule_type": rule_type,
                            "field": field, "severity": rule.get("severity", "critical"),
                            "message": f"字段 {field} 有 {failed} 个值不匹配正则 {pattern}",
                            "failed_count": int(failed), "total_count": len(non_null),
                        })
                        if apply_fixes:
                            df = df.filter(mask)
                            stats["removed_rows"] += int(failed)
                except Exception:
                    pass

        elif rule_type == "range_check":
            min_val = rule.get("min")
            max_val = rule.get("max")
            if field in df.columns:
                non_null = df.filter(pl.col(field).is_not_null())
                mask = None
                if min_val is not None:
                    mask = non_null[field] >= min_val
                if max_val is not None:
                    m = non_null[field] <= max_val
                    mask = mask & m if mask is not None else m
                if mask is not None:
                    failed = (~mask).sum()
                    if failed > 0:
                        violations.append({
                            "rule_id": rule_id, "rule_type": rule_type,
                            "field": field, "severity": rule.get("severity", "critical"),
                            "message": f"字段 {field} 有 {failed} 个值超出范围 [{min_val}, {max_val}]",
                            "failed_count": int(failed), "total_count": len(non_null),
                        })
                        if apply_fixes:
                            df = df.filter(mask)
                            stats["removed_rows"] += int(failed)

        elif rule_type == "uniqueness":
            if field in df.columns:
                non_null = df.filter(pl.col(field).is_not_null())
                dup_mask = non_null[field].is_duplicated()
                failed = dup_mask.sum()
                if failed > 0:
                    violations.append({
                        "rule_id": rule_id, "rule_type": rule_type,
                        "field": field, "severity": rule.get("severity", "critical"),
                        "message": f"字段 {field} 存在 {failed} 个重复值",
                        "failed_count": int(failed), "total_count": len(non_null),
                    })
                    if apply_fixes:
                        df = df.unique(subset=[field], keep="first")
                        stats["removed_rows"] += int(failed)

        elif rule_type == "freshness_check":
            ts_field = rule.get("timestamp_field", "date")
            max_age = rule.get("max_age_minutes", 60)
            if ts_field in df.columns:
                from datetime import timedelta
                non_null = df.filter(pl.col(ts_field).is_not_null())
                cutoff = datetime.now() - timedelta(minutes=max_age)
                try:
                    ts_col = non_null[ts_field].str.to_datetime("%Y-%m-%d", strict=False)
                    old_rows = ts_col < cutoff
                    failed = old_rows.sum()
                    if failed > 0:
                        violations.append({
                            "rule_id": rule_id, "rule_type": rule_type,
                            "field": ts_field, "severity": rule.get("severity", "warning"),
                            "message": f"字段 {ts_field} 有 {failed} 行数据超过 {max_age} 分钟未更新",
                            "failed_count": int(failed), "total_count": len(non_null),
                        })
                        if apply_fixes:
                            df = df.filter(~old_rows)
                            stats["removed_rows"] += int(failed)
                except Exception:
                    pass

    quality_score = compute_quality_score(df, violations)
    return {
        "cleaned_data": df.to_dicts(),
        "violations": violations,
        "quality_score": round(quality_score, 1),
        "stats": stats,
    }


# ============================================================
# 4. 主 ETL Agent 类（API 层）
# ============================================================

class ETLCleanAgent:
    """
    ETL 清洗 Agent：
    - 接受 JSON/CSV 输入
    - 从 rules.yaml 加载规则
    - 执行清洗 + 质量评分
    """

    def __init__(self, db_path: Optional[str] = None, config_dir: Optional[Path] = None):
        if config_dir is None:
            config_dir = Path(__file__).parent
        if db_path is None:
            db_path = str(config_dir.parent / "data" / "warehouse.db")
        self.db_path = db_path
        self.config_dir = config_dir
        self.rules_engine = RulesEngine(db_path, config_dir)

    def process(
        self,
        data: Union[str, bytes, Dict, List],
        content_type: Optional[str] = None,
        rules_override: Optional[List[Dict]] = None,
        apply_fixes: bool = False,
        output_format: str = "json",
    ) -> Dict[str, Any]:
        """
        主处理入口。

        参数：
            data: 输入数据（JSON 字符串、CSV 字符串、dict 或 dict list）
            content_type: 内容类型（可选，用于解析）
            rules_override: 规则覆盖列表（可选，默认从 rules.yaml 加载）
            apply_fixes: 是否自动修复（删除违规行）
            output_format: 输出格式 json | csv | records

        返回：
            {
                "success": True,
                "quality_score": 87.5,
                "total_rows": 1000,
                "violations_count": 3,
                "cleaned_data": [...],
                "format": "json",
                "timestamp": "2026-05-18 19:30:00"
            }
        """
        try:
            # 解析输入
            df = parse_input(data, content_type)
            total_rows = len(df)

            # 获取规则
            if rules_override is not None:
                rules = rules_override
            else:
                config = self.rules_engine.config
                # 从 rules.yaml 加载 defaults + all table_rules
                dqr = config.get("data_quality_rules", {})
                rules = dqr.get("defaults", [])
                for table_name, table_rules in dqr.get("table_rules", {}).items():
                    if table_name == "*":
                        rules.extend([r for r in table_rules if r.get("enabled", True)])
                    for rule in table_rules:
                        if rule.get("enabled", True):
                            rules.append(rule)

            # 清洗
            result = clean_data(df, rules, apply_fixes=apply_fixes)

            # 序列化输出
            cleaned_df = pl.DataFrame(result["cleaned_data"])
            serialized = serialize_output(cleaned_df, output_format)

            return {
                "success": True,
                "quality_score": result["quality_score"],
                "total_rows": total_rows,
                "cleaned_rows": len(cleaned_df),
                "violations_count": len(result["violations"]),
                "violations": result["violations"][:20],  # 最多20条
                "cleaned_data": serialized,
                "format": output_format,
                "stats": result["stats"],
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "quality_score": 0.0,
                "total_rows": 0,
                "violations_count": 0,
                "cleaned_data": None,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }

    def clean_from_db_table(
        self,
        source_table: str,
        rules_override: Optional[List[Dict]] = None,
        apply_fixes: bool = False,
    ) -> Dict[str, Any]:
        """从数据库表加载数据，清洗后保存回数据库"""
        conn = sqlite3.connect(self.db_path)
        try:
            df = pl.read_database(f'SELECT * FROM "{source_table}"', connection=conn)
        finally:
            conn.close()

        result = self.process(
            data=df.to_dicts(),
            rules_override=rules_override,
            apply_fixes=apply_fixes,
        )

        if result["success"] and result["cleaned_data"]:
            cleaned_df = pl.DataFrame(result["cleaned_data"])
            conn2 = sqlite3.connect(self.db_path)
            try:
                cleaned_df.write_database(source_table + "_cleaned", connection=conn2, if_exists="replace")
                result["output_table"] = source_table + "_cleaned"
            finally:
                conn2.close()

        return result


# ============================================================
# 入口验证
# ============================================================

if __name__ == "__main__":
    # 测试：用 sample 数据验证
    sample_data = [
        {"id": 1, "name": "Alice", "email": "alice@example.com", "amount": 150.5, "date": "2026-05-18"},
        {"id": 2, "name": "", "email": "bobatexample.com", "amount": -10, "date": "2026-05-01"},
        {"id": 3, "name": "Charlie", "email": "charlie@test.com", "amount": 200, "date": "2026-05-15"},
        {"id": 4, "name": "Diana", "email": "diana@example.com", "amount": 300, "date": "2026-05-17"},
    ]

    cfg = Path(__file__).parent
    agent = ETLCleanAgent(config_dir=cfg)
    result = agent.process(sample_data, apply_fixes=False)

    print(f"\nETL 清洗结果：")
    print(f"  质量分: {result['quality_score']}")
    print(f"  总行数: {result['total_rows']}")
    print(f"  清洗后: {result['cleaned_rows']}")
    print(f"  违规数: {result['violations_count']}")
    for v in result.get("violations", []):
        print(f"  [{v['severity']}] {v['rule_id']}: {v['message']}")