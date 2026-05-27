#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AutoDataFlow 质量规则配置加载器
==================================
从 YAML 加载质量规则，代码与配置分离。
支持按表名覆盖全局阈值。
"""

import os
import yaml
from pathlib import Path
from typing import Dict, Optional


# ============================================================
# 路径配置
# ============================================================

HERMES_HOME = os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))
DEFAULT_CONFIG_DIR = Path(__file__).parent / "config"


def get_config_dir() -> Path:
    custom = os.environ.get("HERMES_CONFIG_DIR")
    if custom:
        return Path(custom)
    return DEFAULT_CONFIG_DIR


# ============================================================
# 质量规则访问器
# ============================================================

class QualityConfig:
    """
    质量规则配置访问器，惰性加载 YAML。
    线程安全：加载后数据只读。
    """

    _config: Optional[dict] = None

    @classmethod
    def reload(cls):
        cls._config = None

    @classmethod
    def get_config(cls) -> dict:
        if cls._config is None:
            path = get_config_dir() / "quality_rules.yaml"
            if not path.exists():
                raise FileNotFoundError(
                    f"质量规则配置文件不存在: {path}\n"
                    f"请确认 quality_rules.yaml 已创建于 {get_config_dir()}"
                )
            with open(path, encoding="utf-8") as f:
                cls._config = yaml.safe_load(f)
        return cls._config

    # ---- 权重 ----
    @classmethod
    def weights(cls) -> dict:
        return cls.get_config().get("scoring_weights", {})

    @classmethod
    def null_weight(cls) -> float:
        return cls.weights().get("null_rate", 0.4)

    @classmethod
    def outlier_weight(cls) -> float:
        return cls.weights().get("outlier_rate", 0.35)

    @classmethod
    def uniqueness_weight(cls) -> float:
        return cls.weights().get("uniqueness", 0.25)

    # ---- 阈值 ----
    @classmethod
    def null_thresholds(cls) -> dict:
        return cls.get_config().get("null_thresholds", {"critical": 0.3, "warning": 0.1})

    @classmethod
    def outlier_thresholds(cls) -> dict:
        return cls.get_config().get("outlier_thresholds", {"critical": 0.05, "warning": 0.02})

    @classmethod
    def alert_thresholds(cls) -> dict:
        return cls.get_config().get("alert_thresholds", {
            "quality_score_min": 70,
            "null_rate_max": 0.05,
            "outlier_count_max": 10,
        })

    @classmethod
    def quality_grades(cls) -> dict:
        return cls.get_config().get("quality_grades", {
            "excellent": 90, "good": 75, "fair": 60, "poor": 0
        })

    # ---- 表级规则（可覆盖全局） ----
    @classmethod
    def table_rule(cls, table_name: str) -> dict:
        """获取指定表的规则，没有则返回空字典（用全局默认值）"""
        table_rules = cls.get_config().get("table_rules", {})
        return table_rules.get(table_name, {})

    @classmethod
    def null_threshold_for(cls, table_name: str) -> float:
        """该表的 null 率告警阈值"""
        table_cfg = cls.table_rule(table_name)
        if "null_threshold" in table_cfg:
            return table_cfg["null_threshold"]
        return cls.null_thresholds()["critical"]

    @classmethod
    def outlier_threshold_for(cls, table_name: str) -> float:
        """该表的异常值率告警阈值"""
        table_cfg = cls.table_rule(table_name)
        if "outlier_threshold" in table_cfg:
            return table_cfg["outlier_threshold"]
        return cls.outlier_thresholds()["critical"]

    @classmethod
    def grade(cls, score: float) -> str:
        """根据分数返回等级标签"""
        grades = cls.quality_grades()
        if score >= grades.get("excellent", 90):
            return "excellent"
        if score >= grades.get("good", 75):
            return "good"
        if score >= grades.get("fair", 60):
            return "fair"
        return "poor"


if __name__ == "__main__":
    print("=" * 60)
    print("AutoDataFlow 质量规则配置加载器")
    print("=" * 60)
    print(f"\n配置目录: {get_config_dir()}")
    cfg = QualityConfig.get_config()
    print(f"权重: {cfg['scoring_weights']}")
    print(f"告警阈值: {cfg['alert_thresholds']}")
    print(f"示例等级: {QualityConfig.grade(85)}")
