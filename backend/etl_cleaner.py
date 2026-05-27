#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AutoDataFlow ETL 多目标数据清洗引擎
====================================
基于 Polars 的高性能多目标数据清洗：

1. 多目标输出：一次输入 → 多个清洗后的目标输出（ODS / DWD / ADS）
2. 规则驱动清洗：应用 data_quality_rules 规则进行清洗
3. 质量评分：对清洗后的数据打分，输出质量报告
4. 血缘追踪：记录每个字段的清洗血缘关系

使用方式：
    cleaner = ETLCleaner("data/warehouse.db", "backend/config")
    result = cleaner.clean(
        source_table="ods_sales",
        targets=[
            {"name": "dwd_sales", "rules": ["drop_nulls", "outlier_clip"]},
            {"name": "ads_sales", "rules": ["deduplicate", "aggregate"]},
        ]
    )
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
import polars as pl

from rule_engine import DataQualityRuleEngine, RuleViolation


# ============================================================
# 1. 数据结构
# ============================================================

@dataclass
class CleaningOperation:
    """一次清洗操作"""
    name: str                       # 操作名称
    description: str                 # 描述
    before_count: int               # 清洗前行数
    after_count: int                # 清洗后行数
    removed_count: int              # 删除的行数
    modified_count: int             # 修改的记录数
    operations_log: List[str]       # 具体操作日志
    quality_score_before: float     # 清洗前质量分
    quality_score_after: float      # 清洗后质量分
    target: str                     # 输出目标表名

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "before_count": self.before_count,
            "after_count": self.after_count,
            "removed_count": self.removed_count,
            "modified_count": self.modified_count,
            "operations_log": self.operations_log,
            "quality_score_before": round(self.quality_score_before, 2),
            "quality_score_after": round(self.quality_score_after, 2),
            "target": self.target,
            "improvement": round(self.quality_score_after - self.quality_score_before, 2),
        }


@dataclass
class ETLCleanResult:
    """ETL 清洗结果"""
    source_table: str
    targets: List[CleaningOperation]
    overall_quality_score: float
    lineage_records: List[Dict]
    timestamp: str

    def to_dict(self) -> dict:
        return {
            "source_table": self.source_table,
            "targets": [t.to_dict() for t in self.targets],
            "overall_quality_score": round(self.overall_quality_score, 2),
            "lineage_records": self.lineage_records,
            "timestamp": self.timestamp,
        }


# ============================================================
# 2. 清洗策略
# ============================================================

class CleaningStrategy:
    """可配置的清洗策略"""

    STRATEGIES = {
        # 空值处理
        "fill_null_median":      "用中位数填充数值列空值",
        "fill_null_mean":        "用均值填充数值列空值",
        "fill_null_zero":        "用 0 填充数值列空值",
        "fill_null_forward":     "用前向填充（Last Observation Carried Forward）",
        "drop_nulls":           "删除含有空值的行",
        "flag_nulls":            "新增 _is_null 标记列（不删除）",

        # 异常值处理
        "outlier_clip":          "用 IQR 边界Clip异常值（代替删除）",
        "outlier_drop":          "删除异常值行",
        "outlier_flag":          "新增 _is_outlier 标记列",
        "outlier_winsorize":     "Winsorize 缩尾处理（保留数据，限制极端值）",

        # 去重
        "deduplicate":           "基于主键去重（保留首条）",
        "deduplicate_all":       "完全去重（所有字段相同才算重复）",

        # 类型处理
        "cast_datetime":         "尝试将字符串列解析为 datetime",
        "cast_numeric":          "尝试将字符串列转换为数值",
        "trim_strings":          "去除字符串列的首尾空格",

        # 聚合（ADS 层用）
        "aggregate_sum":         "按维度聚合求和",
        "aggregate_avg":         "按维度聚合求均值",
        "aggregate_count":       "按维度聚合计数",
    }

    @classmethod
    def apply(cls, df: pl.DataFrame, strategy_names: List[str]) -> Tuple[pl.DataFrame, List[str]]:
        """对 DataFrame 应用一系列清洗策略"""
        ops_log: List[str] = []
        modified = 0
        result = df

        for name in strategy_names:
            if name == "drop_nulls":
                before = len(result)
                result = result.drop_nulls()
                removed = before - len(result)
                if removed > 0:
                    ops_log.append(f"drop_nulls: 删除了 {removed} 行空值记录")
                    modified += removed
            elif name == "fill_null_median":
                for col in result.columns:
                    s = result[col]
                    if s.dtype in (pl.Int64, pl.Int32, pl.Float64, pl.Float32):
                        median = s.median()
                        result = result.with_columns(s.fill_null(median))
                        modified += int(s.null_count())
                ops_log.append("fill_null_median: 数值列空值用中位数填充")
            elif name == "fill_null_mean":
                for col in result.columns:
                    s = result[col]
                    if s.dtype in (pl.Int64, pl.Int32, pl.Float64, pl.Float32):
                        mean = s.mean()
                        result = result.with_columns(s.fill_null(mean))
                        modified += int(s.null_count())
                ops_log.append("fill_null_mean: 数值列空值用均值填充")
            elif name == "fill_null_zero":
                for col in result.columns:
                    s = result[col]
                    if s.dtype in (pl.Int64, pl.Int32, pl.Float64, pl.Float32):
                        result = result.with_columns(s.fill_null(0))
                        modified += int(s.null_count())
                ops_log.append("fill_null_zero: 数值列空值填充为 0")
            elif name == "flag_nulls":
                for col in result.columns:
                    null_count = result[col].null_count()
                    if null_count > 0:
                        result = result.with_columns(
                            pl.col(col).is_null().alias(f"{col}_is_null")
                        )
                ops_log.append("flag_nulls: 新增 _is_null 标记列")
            elif name == "outlier_clip":
                for col in result.columns:
                    s = result[col]
                    if s.dtype in (pl.Int64, pl.Int32, pl.Float64, pl.Float32):
                        q1 = s.quantile(0.25)
                        q3 = s.quantile(0.75)
                        iqr = q3 - q1
                        lower = q1 - 1.5 * iqr
                        upper = q3 + 1.5 * iqr
                        before_clip = (~s.is_between(lower, upper)).sum()
                        if before_clip > 0:
                            result = result.with_columns(
                                s.clip(lower, upper).alias(col)
                            )
                            modified += int(before_clip)
                ops_log.append("outlier_clip: IQR 边界Clip异常值")
            elif name == "outlier_drop":
                before = len(result)
                mask = None
                for col in result.columns:
                    s = result[col]
                    if s.dtype in (pl.Int64, pl.Int32, pl.Float64, pl.Float32):
                        q1 = s.quantile(0.25)
                        q3 = s.quantile(0.75)
                        iqr = q3 - q1
                        lower = q1 - 1.5 * iqr
                        upper = q3 + 1.5 * iqr
                        m = s.is_between(lower, upper)
                        mask = m if mask is None else (mask & m)
                if mask is not None:
                    result = result.filter(mask)
                    removed = before - len(result)
                    if removed > 0:
                        ops_log.append(f"outlier_drop: 删除了 {removed} 行异常值")
                        modified += removed
            elif name == "outlier_flag":
                for col in result.columns:
                    s = result[col]
                    if s.dtype in (pl.Int64, pl.Int32, pl.Float64, pl.Float32):
                        q1 = s.quantile(0.25)
                        q3 = s.quantile(0.75)
                        iqr = q3 - q1
                        lower = q1 - 1.5 * iqr
                        upper = q3 + 1.5 * iqr
                        outlier_count = (~s.is_between(lower, upper)).sum()
                        if outlier_count > 0:
                            result = result.with_columns(
                                (~s.is_between(lower, upper)).alias(f"{col}_is_outlier")
                            )
                ops_log.append("outlier_flag: 新增 _is_outlier 标记列")
            elif name == "outlier_winsorize":
                for col in result.columns:
                    s = result[col]
                    if s.dtype in (pl.Int64, pl.Int32, pl.Float64, pl.Float32):
                        q1 = s.quantile(0.25)
                        q3 = s.quantile(0.75)
                        iqr = q3 - q1
                        lower = q1 - 1.5 * iqr
                        upper = q3 + 1.5 * iqr
                        result = result.with_columns(
                            s.clip(lower, upper).alias(col)
                        )
                ops_log.append("outlier_winsorize: Winsorize 缩尾处理")
            elif name == "deduplicate":
                before = len(result)
                # 找第一列作为主键（实际应指定）
                pk = result.columns[0]
                result = result.unique(subset=[pk], keep="first")
                removed = before - len(result)
                if removed > 0:
                    ops_log.append(f"deduplicate: 以 {pk} 为主键去重，删除了 {removed} 条重复")
                    modified += removed
            elif name == "trim_strings":
                for col in result.columns:
                    s = result[col]
                    if s.dtype == pl.Utf8:
                        trimmed = s.str.strip()
                        changed = (trimmed != s).sum()
                        if changed > 0:
                            result = result.with_columns(trimmed.alias(col))
                            modified += int(changed)
                ops_log.append("trim_strings: 去除字符串首尾空格")
            else:
                ops_log.append(f"unknown_strategy: '{name}' 未识别，跳过")

        return result, ops_log, modified


# ============================================================
# 3. 质量评分器
# ============================================================

class QualityScorer:
    """基于规则引擎的质量评分"""

    @staticmethod
    def score(df: pl.DataFrame, engine: DataQualityRuleEngine, table: str) -> float:
        """用规则引擎评估质量分数"""
        result = engine.validate_table(table)
        return result.quality_score

    @staticmethod
    def simple_score(df: pl.DataFrame) -> float:
        """快速评分（不依赖规则引擎）"""
        if len(df) == 0:
            return 0.0
        total_cols = len(df.columns)
        null_score = 0.0
        outlier_score = 0.0
        for col in df.columns:
            s = df[col]
            total = len(s)
            null_pct = s.null_count() / total if total > 0 else 0
            null_score += max(0, 100 - null_pct * 100)
            if s.dtype in (pl.Int64, pl.Int32, pl.Float64, pl.Float32):
                q1 = s.quantile(0.25)
                q3 = s.quantile(0.75)
                iqr = q3 - q1
                lower = q1 - 1.5 * iqr
                upper = q3 + 1.5 * iqr
                outlier_pct = (~s.is_between(lower, upper)).sum() / total
                outlier_score += max(0, 100 - outlier_pct * 100)
            else:
                outlier_score += 100
        return (null_score + outlier_score) / (2 * total_cols) if total_cols > 0 else 0


# ============================================================
# 4. 多目标 ETL 清洗引擎
# ============================================================

class ETLCleaner:
    """
    多目标 ETL 数据清洗引擎：
    - 一次读取，多目标输出
    - 规则引擎驱动 + 清洗策略组合
    - 血缘追踪
    - 质量评分
    """

    def __init__(self, db_path: str, config_dir: Path):
        self.db_path = db_path
        self.config_dir = config_dir
        self.rule_engine = DataQualityRuleEngine(db_path, config_dir)

    def _load_source(self, table: str) -> Optional[pl.DataFrame]:
        conn = sqlite3.connect(self.db_path)
        try:
            df = pl.read_database(f'SELECT * FROM "{table}"', connection=conn)
            return df
        except Exception:
            return None
        finally:
            conn.close()

    def _save_target(self, df: pl.DataFrame, table: str, if_exists: str = "replace"):
        """保存清洗后的 DataFrame 到 SQLite"""
        conn = sqlite3.connect(self.db_path)
        try:
            df.write_database(table, connection=conn, if_exists=if_exists)
        finally:
            conn.close()

    def clean(
        self,
        source_table: str,
        targets: List[Dict[str, Any]],
        compute_quality: bool = True,
    ) -> ETLCleanResult:
        """
        多目标清洗主入口

        参数：
            source_table: 源表名
            targets: 目标列表，每个元素：
                {
                    "name": "dwd_sales",
                    "rules": ["drop_nulls", "outlier_clip"],
                    "quality_threshold": 80.0,   # 可选，质量门槛
                }

        返回：
            ETLCleanResult，包含每个目标的清洗操作详情
        """
        source_df = self._load_source(source_table)
        if source_df is None:
            raise ValueError(f"源表 {source_table} 不存在或无法加载")

        before_score = QualityScorer.simple_score(source_df) if compute_quality else 0.0
        before_count = len(source_df)
        lineage_records: List[Dict] = []
        operations_list: List[CleaningOperation] = []
        total_improvement = 0.0

        for target in targets:
            target_name = target["name"]
            strategies = target.get("rules", [])
            threshold = target.get("quality_threshold", 0.0)

            # 深拷贝，避免修改原始数据
            df_clean = source_df.clone()

            # 应用清洗策略
            df_clean, ops_log, modified = CleaningStrategy.apply(df_clean, strategies)

            after_count = len(df_clean)
            removed = before_count - after_count
            after_score = QualityScorer.simple_score(df_clean) if compute_quality else 0.0
            improvement = after_score - before_score

            operation = CleaningOperation(
                name=f"clean_to_{target_name}",
                description=f"从 {source_table} 清洗至 {target_name}，应用策略: {strategies}",
                before_count=before_count,
                after_count=after_count,
                removed_count=removed,
                modified_count=modified,
                operations_log=ops_log,
                quality_score_before=before_score,
                quality_score_after=after_score,
                target=target_name,
            )
            operations_list.append(operation)
            total_improvement += improvement

            # 保存目标表
            self._save_target(df_clean, target_name, if_exists="replace")

            # 记录血缘
            for col in df_clean.columns:
                lineage_records.append({
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "source_table": source_table,
                    "source_column": col,
                    "target_table": target_name,
                    "target_column": col,
                    "operation": f"ETL_clean:{target_name}",
                    "transformations": strategies,
                })

        avg_score = sum(op.quality_score_after for op in operations_list) / len(operations_list)
        return ETLCleanResult(
            source_table=source_table,
            targets=operations_list,
            overall_quality_score=avg_score,
            lineage_records=lineage_records,
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

    def clean_single(
        self,
        source_table: str,
        output_table: str,
        strategies: List[str],
    ) -> CleaningOperation:
        """单目标清洗（便捷方法）"""
        source_df = self._load_source(source_table)
        if source_df is None:
            raise ValueError(f"源表 {source_table} 不存在")

        before_score = QualityScorer.simple_score(source_df)
        before_count = len(source_df)

        df_clean, ops_log, modified = CleaningStrategy.apply(source_df.clone(), strategies)
        after_count = len(df_clean)
        after_score = QualityScorer.simple_score(df_clean)

        self._save_target(df_clean, output_table, if_exists="replace")

        return CleaningOperation(
            name=f"clean_to_{output_table}",
            description=f"清洗至 {output_table}",
            before_count=before_count,
            after_count=after_count,
            removed_count=before_count - after_count,
            modified_count=modified,
            operations_log=ops_log,
            quality_score_before=before_score,
            quality_score_after=after_score,
            target=output_table,
        )


# ============================================================
# 入口（验证）
# ============================================================

if __name__ == "__main__":
    db = str(Path(__file__).parent / "data" / "warehouse.db")
    cfg = Path(__file__).parent / "config"
    cleaner = ETLCleaner(db, cfg)

    # 检查有哪些表
    conn = sqlite3.connect(db)
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    tables = [r[0] for r in cursor.fetchall()]
    conn.close()

    if tables:
        result = cleaner.clean(
            source_table=tables[0],
            targets=[
                {"name": "dwd_test", "rules": ["drop_nulls", "outlier_clip", "trim_strings"]},
                {"name": "ads_test", "rules": ["deduplicate", "fill_null_median"]},
            ]
        )
        print(f"\n清洗完成: {result.source_table} → {[t.target for t in result.targets]}")
        print(f"总体质量分: {result.overall_quality_score:.1f}")
        for op in result.targets:
            print(f"\n  → {op.target}:")
            print(f"    质量分: {op.quality_score_before:.1f} → {op.quality_score_after:.1f} "
                  f"({op.quality_score_after - op.quality_score_before:+.1f})")
            print(f"    行数: {op.before_count} → {op.after_count} (-{op.removed_count})")
            for log in op.operations_log:
                print(f"    - {log}")