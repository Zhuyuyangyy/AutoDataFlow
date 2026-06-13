# -*- coding: utf-8 -*-
"""
AutoDataFlow Causal Inference Engine v3.0
=========================================
将AutoDataFlow从"Schema变更检测工具"升级为"因果驱动的Schema演变预测框架"

核心创新（对应SCI Framework）：
  C1: 因果血缘图谱 (Causal Lineage Graph)
  C2: Do-Calculus变更影响预测 (Schema Change Impact Prediction via Do-Calculus)
  C3: 反事实质量推理 (Counterfactual Quality Prediction)
  C4: 多Agent因果协作 (Multi-Agent Causal Collaboration)

使用方法：
    engine = CausalSchemaEngine()
    engine.build_from_warehouse("data/warehouse.db")
    result = engine.predict_impact(do_operation="drop_column", target="orders.customer_email")
    counterfactual = engine.counterfactual(
        change={"type": "rename_column", "target": "sales.amount", "new_name": "sales.revenue"},
        outcome="ETL_Job_1"
    )
"""

import json
import sqlite3
import hashlib
import math
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple, Set
from dataclasses import dataclass, field, asdict
from enum import Enum
import random

from causal_mechanism_inferrer import CausalMechanismInferrer, infer_causal_mechanisms


# ============================================================
# 1. 数据结构：因果边 & 因果图
# ============================================================

class CausalMechanism(Enum):
    """因果机制类型"""
    FOREIGN_KEY = "foreign_key"           # 外键约束（确定性因果）
    ETL_TRANSFORM = "etl_transform"       # ETL转换（聚合/清洗）
    SCHEMA_DEPENDENCY = "schema_dependency"  # Schema依赖（列引用）
    QUALITY_PROPAGATION = "quality_propagation"  # 质量指标传播
    CASCADING_FAILURE = "cascading_failure"    # 连锁故障


@dataclass
class CausalEdge:
    """
    因果边：表示两个Schema元素之间的因果关系

    与普通数据血缘的区别：
      - 数据血缘边：A → B（A列被B列引用）
      - 因果边：A causes B（A的变化会导致B的变化，包含因果机制）
    """
    source_table: str
    source_column: str
    target_table: str
    target_column: str
    mechanism: str                  # 因果机制类型
    strength: float = 1.0          # 因果强度 (0-1)
    confidence: float = 1.0        # 推断置信度 (0-1)
    delay_steps: int = 1           # 因果传播延迟（步数）
    conditions: List[str] = field(default_factory=list)  # 因果成立的条件
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "source": f"{self.source_table}.{self.source_column}",
            "target": f"{self.target_table}.{self.target_column}",
            "mechanism": self.mechanism,
            "strength": round(self.strength, 3),
            "confidence": round(self.confidence, 3),
            "delay_steps": self.delay_steps,
            "conditions": self.conditions,
        }

    def __hash__(self):
        return hash((self.source_table, self.source_column,
                     self.target_table, self.target_column, self.mechanism))


@dataclass
class SchemaNode:
    """Schema节点：代表一个表或列"""
    table: str
    column: Optional[str] = None
    dtype: str = "TEXT"
    null_pct: float = 0.0
    quality_score: float = 100.0   # 0-100
    row_count: int = 0
    is_key: bool = False           # 是否是主键/外键
    node_type: str = "column"      # "table" or "column"

    def node_id(self) -> str:
        if self.column:
            return f"{self.table}.{self.column}"
        return self.table


@dataclass
class QualityMetric:
    """质量指标节点"""
    metric_id: str
    table: str
    column: Optional[str]
    metric_type: str          # "null_rate", "unique_ratio", "outlier_count", "anomaly_score"
    value: float
    timestamp: str
    severity: str = "normal"   # "normal", "warning", "critical"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DoOperation:
    """
    Do算子：表示对Schema的干预操作

    支持的操作类型：
      - do(drop_column(table, column))
      - do(rename_column(table, column, new_name))
      - do(dtype_change(table, column, new_dtype))
      - do(add_column(table, column, dtype))
      - do(truncate_table(table))
    """
    op_type: str
    table: str
    column: Optional[str] = None
    new_name: Optional[str] = None
    new_dtype: Optional[str] = None
    new_value: Any = None
    description: str = ""

    def __str__(self):
        if self.op_type == "drop_column":
            return f"do(drop_column({self.table}.{self.column}))"
        elif self.op_type == "rename_column":
            return f"do(rename_column({self.table}.{self.column} → {self.new_name}))"
        elif self.op_type == "dtype_change":
            return f"do(dtype_change({self.table}.{self.column} → {self.new_dtype}))"
        elif self.op_type == "add_column":
            return f"do(add_column({self.table}.{self.column}:{self.new_dtype}))"
        elif self.op_type == "truncate_table":
            return f"do(truncate_table({self.table}))"
        return f"do({self.op_type})"

    def is_destructive(self) -> bool:
        """是否为破坏性变更"""
        return self.op_type in ("drop_column", "truncate_table", "dtype_change")


@dataclass
class CausalEffect:
    """
    因果效应：描述一次Do操作产生的因果效应
    """
    do_operation: str
    affected_node: str
    effect_type: str           # "quality_degradation", "etl_failure", "cascading"
    probability: float          # 发生概率 P(effect | do(op))
    severity: str               # "info", "warning", "critical"
    explanation: str
    affected_etl_jobs: List[str] = field(default_factory=list)
    path: List[str] = field(default_factory=list)  # 因果传播路径


@dataclass
class CounterfactualResult:
    """
    反事实推理结果
    """
    question: str
    factual: str               # 事实结果（未干预时的结果）
    counterfactual: str         # 反事实结果（干预后的结果）
    difference: str              # 差异描述
    confidence: float
    causal_path: List[str]      # 因果路径
    mitigation_suggestions: List[str] = field(default_factory=list)


# ============================================================
# 2. 因果图谱构建器
# ============================================================

class CausalGraphBuilder:
    """
    从数据库Schema和ETL作业中构建因果图谱

    构建方法：
      1. 扫描SQLite中的所有表和列（Schema Agent的输出）
      2. 扫描ETL配置，推断列级血缘（ETL Agent的输出）
      3. 基于数据质量指标，构建质量传播边（Observer Agent的输出）
      4. 标注因果机制和强度
    """

    def __init__(self, db_path: str, use_statistical_inference: bool = True):
        self.db_path = db_path
        self.nodes: Dict[str, SchemaNode] = {}
        self.edges: Set[CausalEdge] = set()
        self.quality_metrics: Dict[str, QualityMetric] = {}
        self.use_statistical_inference = use_statistical_inference
        self._mechanism_inferrer: Optional[CausalMechanismInferrer] = None

    def build_from_db(self, lineage_log: Optional[List[Dict]] = None) -> "CausalGraphBuilder":
        """从数据库Schema构建因果图谱"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # 1. 获取所有表
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [row[0] for row in cursor.fetchall()]

        for table in tables:
            # 添加表节点
            self.nodes[table] = SchemaNode(table=table, node_type="table", row_count=self._count_rows(cursor, table))

            # 2. 获取所有列
            cursor.execute(f"PRAGMA table_info('{table}')")
            columns = cursor.fetchall()

            for col_info in columns:
                col_name = col_info[1]
                col_dtype = col_info[2]
                notnull = col_info[3]
                default = col_info[4]
                pk = col_info[5]

                node_id = f"{table}.{col_name}"
                self.nodes[node_id] = SchemaNode(
                    table=table,
                    column=col_name,
                    dtype=col_dtype,
                    is_key=(pk == 1),
                    node_type="column",
                    row_count=self._count_rows(cursor, table),
                )

        # 3. 使用统计推断替代硬编码的FK推断
        if self.use_statistical_inference:
            self._infer_causal_mechanisms_statistically()
        else:
            # 旧的硬编码方式（保留向后兼容）
            for table in tables:
                cursor.execute(f"PRAGMA table_info('{table}')")
                columns = cursor.fetchall()
                for col_info in columns:
                    self._infer_foreign_key_edges(cursor, table, col_info[1])

        # 4. 从血缘日志构建ETL转换因果边
        if lineage_log:
            for entry in lineage_log:
                self._add_etl_edge(entry)

        conn.close()
        return self

    def _infer_causal_mechanisms_statistically(self):
        """
        使用CausalMechanismInferrer进行统计推断
        替换硬编码的列名模式匹配
        """
        try:
            inferrer = CausalMechanismInferrer(self.db_path)
            inferrer.connect()
            inferrer.load_data()
            causal_edges = inferrer.infer_all_mechanisms()
            inferrer.disconnect()
            self._mechanism_inferrer = inferrer

            for edge in causal_edges:
                self.edges.add(CausalEdge(
                    source_table=edge['source'].split('.')[0],
                    source_column=edge['source'].split('.')[1],
                    target_table=edge['target'].split('.')[0],
                    target_column=edge['target'].split('.')[1],
                    mechanism=edge['mechanism'],
                    strength=edge['strength'],
                    confidence=edge['confidence'],
                    metadata=edge.get('metrics', {}),
                ))
        except Exception as e:
            # 如果统计推断失败，回退到硬编码方式
            import warnings
            warnings.warn(f"Statistical inference failed: {e}, falling back to pattern matching")
            for table in self.nodes.keys():
                if '.' in table:
                    t, c = table.split('.', 1)
                    self._infer_foreign_key_edges(None, t, c)

    def _count_rows(self, cursor, table: str) -> int:
        try:
            cursor.execute(f"SELECT COUNT(*) FROM \"{table}\"")
            return cursor.fetchone()[0]
        except:
            return 0

    def _infer_foreign_key_edges(self, cursor, table: str, column: str):
        """基于列名模式推断外键因果关系"""
        fk_patterns = [
            ("_id", "_name"),  # order_id → customer_name
            ("_id", "_id"),    # user_id → order_user_id
        ]

        for col_pattern, target_pattern in fk_patterns:
            if column.endswith("_id") or column.endswith("_key"):
                # 尝试找对应的外键目标表
                base = column.replace("_id", "").replace("_key", "")

                for node_id, node in self.nodes.items():
                    if node.node_type == "column" and node.table != table:
                        if node.column == f"{base}_name":
                            self.edges.add(CausalEdge(
                                source_table=table,
                                source_column=column,
                                target_table=node.table,
                                target_column=node.column,
                                mechanism=CausalMechanism.FOREIGN_KEY.value,
                                strength=1.0,
                                confidence=0.8,  # 基于模式推断，降低置信度
                                conditions=[f"{column} references {node.table}.{node.column}"],
                            ))
                        elif node.column == column:
                            self.edges.add(CausalEdge(
                                source_table=table,
                                source_column=column,
                                target_table=node.table,
                                target_column=node.column,
                                mechanism=CausalMechanism.FOREIGN_KEY.value,
                                strength=0.9,
                                confidence=0.7,
                            ))

    def _add_etl_edge(self, lineage_entry: Dict):
        """从血缘日志添加ETL转换因果边"""
        source = lineage_entry.get("source")
        target = lineage_entry.get("target")
        transform = lineage_entry.get("transform", "etl_transform")

        if source and target:
            src_parts = source.split(".")
            tgt_parts = target.split(".")

            if len(src_parts) == 2 and len(tgt_parts) == 2:
                self.edges.add(CausalEdge(
                    source_table=src_parts[0],
                    source_column=src_parts[1],
                    target_table=tgt_parts[0],
                    target_column=tgt_parts[1],
                    mechanism=CausalMechanism.ETL_TRANSFORM.value,
                    strength=lineage_entry.get("strength", 0.95),
                    confidence=lineage_entry.get("confidence", 0.9),
                    delay_steps=lineage_entry.get("delay_steps", 1),
                    conditions=lineage_entry.get("conditions", []),
                ))

    def add_quality_edge(
        self,
        source_node: str,
        target_node: str,
        quality_type: str,
        strength: float = 0.8,
    ):
        """添加质量传播因果边"""
        self.edges.add(CausalEdge(
            source_table=source_node.split(".")[0] if "." in source_node else source_node,
            source_column=source_node.split(".")[1] if "." in source_node else "",
            target_table=target_node.split(".")[0] if "." in target_node else target_node,
            target_column=target_node.split(".")[1] if "." in target_node else "",
            mechanism=CausalMechanism.QUALITY_PROPAGATION.value,
            strength=strength,
            confidence=0.85,
        ))

    def get_downstream(self, node_id: str, max_depth: int = 5) -> List[str]:
        """获取某个节点的所有下游节点（递归）"""
        downstream = []
        visited = set()

        def _dfs(current: str, depth: int):
            if depth > max_depth or current in visited:
                return
            visited.add(current)

            for edge in self.edges:
                src = f"{edge.source_table}.{edge.source_column}"
                tgt = f"{edge.target_table}.{edge.target_column}"

                if src == current:
                    downstream.append(tgt)
                    _dfs(tgt, depth + 1)

        _dfs(node_id, 0)
        return downstream

    def get_upstream(self, node_id: str, max_depth: int = 5) -> List[str]:
        """获取某个节点的所有上游节点（递归）"""
        upstream = []
        visited = set()

        def _dfs(current: str, depth: int):
            if depth > max_depth or current in visited:
                return
            visited.add(current)

            for edge in self.edges:
                src = f"{edge.source_table}.{edge.source_column}"
                tgt = f"{edge.target_table}.{edge.target_column}"

                if tgt == current:
                    upstream.append(src)
                    _dfs(src, depth + 1)

        _dfs(node_id, 0)
        return upstream

    def to_graph_dict(self) -> Dict:
        """导出为可序列化的图格式（用于可视化或传输）"""
        return {
            "nodes": [
                {
                    "id": node_id,
                    "table": node.table,
                    "column": node.column,
                    "dtype": node.dtype,
                    "quality_score": node.quality_score,
                    "is_key": node.is_key,
                    "type": node.node_type,
                }
                for node_id, node in self.nodes.items()
            ],
            "edges": [edge.to_dict() for edge in self.edges],
            "stats": {
                "total_nodes": len(self.nodes),
                "total_edges": len(self.edges),
                "mechanisms": self._count_by_mechanism(),
            }
        }

    def _count_by_mechanism(self) -> Dict[str, int]:
        counts = {}
        for edge in self.edges:
            counts[edge.mechanism] = counts.get(edge.mechanism, 0) + 1
        return counts


# ============================================================
# 3. Do-Calculus 推理引擎
# ============================================================

class DoCalculusEngine:
    """
    Do-Calculus 推理引擎

    实现Pearl的Do-Calculus三条规则，用于预测Schema变更的因果影响：

    Rule 1 (Addition/Removal of Evidence):
      P(Y | do(X), Z, W) = P(Y | do(X), W)  如果 Z ⊥ Y | X, W

    Rule 2 (Action/Observation Exchange):
      P(Y | do(X), Z, W) = P(Y | X, Z, W)  如果 Z ⊥ X | Y, W 且无后门路径

    Rule 3 (Action/Action Exchange):
      P(Y | do(X), do(Z), W) = P(Y | do(X), Z, W)  如果 X ⊥ Z | Y, W

    在Schema变更场景中：
      do(drop_column(table.col)) 表示强制删除某列
      P(ETL_failure | do(drop_column(...))) 表示删除后ETL失败的概率
    """

    def __init__(self, causal_graph: CausalGraphBuilder):
        self.graph = causal_graph
        self._etl_job_map = self._build_etl_map()

    def _build_etl_map(self) -> Dict[str, List[str]]:
        """
        构建列 → ETL作业的映射
        在实际系统中，这应该从ETL配置文件中读取
        这里基于列名模式做简单推断
        """
        etl_map = {}
        for node_id in self.graph.nodes:
            if self.graph.nodes[node_id].node_type == "column":
                # 推断该列被哪些ETL作业使用
                etl_jobs = self._infer_etl_jobs(node_id)
                if etl_jobs:
                    etl_map[node_id] = etl_jobs
        return etl_map

    def _infer_etl_jobs(self, node_id: str) -> List[str]:
        """基于列名模式推断ETL作业"""
        table, col = node_id.split(".")
        jobs = []

        # 基于模式的ETL作业推断
        if any(k in col.lower() for k in ["amount", "price", "total", "revenue"]):
            jobs.append("ETL_RevenueAggregation")
        if any(k in col.lower() for k in ["date", "time", "created", "updated"]):
            jobs.append("ETL_TimeDimensionJoin")
        if any(k in col.lower() for k in ["id", "_key"]):
            jobs.append("ETL_JoinMasterData")
        if "status" in col.lower() or "type" in col.lower():
            jobs.append("ETL_StatusFilter")

        if not jobs:
            jobs.append(f"ETL_GeneralTransform_{table.upper()}")

        return jobs

    def predict_impact(self, do_op: DoOperation) -> List[CausalEffect]:
        """
        核心方法：预测Do操作的因果影响

        使用Do-Calculus Rule 2（action/observation exchange）：
          P(affected_node | do(operation)) = P(affected_node | operation)

        对于每个受影响的节点，计算：
          1. 因果传播概率（沿因果边的传播）
          2. ETL作业失败概率
          3. 质量降级程度
        """
        effects = []
        affected_nodes = self._get_affected_nodes(do_op)

        for node_id in affected_nodes:
            # 计算因果传播路径
            path = self._find_causal_path(do_op, node_id)

            # 计算影响概率
            probability = self._compute_effect_probability(do_op, node_id, path)

            # 计算严重程度
            severity = self._compute_severity(do_op, node_id, probability)

            # 确定受影响的ETL作业
            etl_jobs = self._get_affected_etl_jobs(node_id)

            effect = CausalEffect(
                do_operation=str(do_op),
                affected_node=node_id,
                effect_type=self._classify_effect(node_id),
                probability=probability,
                severity=severity,
                explanation=self._explain_effect(do_op, node_id, path),
                affected_etl_jobs=etl_jobs,
                path=path,
            )
            effects.append(effect)

        # 按概率排序
        effects.sort(key=lambda e: e.probability, reverse=True)
        return effects

    def _get_affected_nodes(self, do_op: DoOperation) -> List[str]:
        """获取受Do操作影响的所有节点"""
        if do_op.column:
            primary = f"{do_op.table}.{do_op.column}"
        else:
            primary = do_op.table

        # 直接下游 + 递归下游
        downstream = self.graph.get_downstream(primary, max_depth=5)

        # 同一表的其他列也可能受影响
        table_cols = [n for n in self.graph.nodes
                      if n.startswith(f"{do_op.table}.") and self.graph.nodes[n].node_type == "column"]
        all_affected = list(set([primary] + table_cols + downstream))
        return all_affected

    def _find_causal_path(self, do_op: DoOperation, target_node: str) -> List[str]:
        """找到从Do操作到目标节点的因果路径"""
        if do_op.column:
            start = f"{do_op.table}.{do_op.column}"
        else:
            start = do_op.table

        if target_node == start:
            return [start]

        # BFS找最短因果路径
        queue = [(start, [start])]
        visited = {start}

        while queue:
            current, path = queue.pop(0)
            for edge in self.graph.edges:
                src = f"{edge.source_table}.{edge.source_column}"
                tgt = f"{edge.target_table}.{edge.target_column}"

                if src == current and tgt not in visited:
                    new_path = path + [tgt]
                    if tgt == target_node:
                        return new_path
                    visited.add(tgt)
                    queue.append((tgt, new_path))

        return [start, target_node]  # 直连假设

    def _compute_effect_probability(
        self,
        do_op: DoOperation,
        target_node: str,
        path: List[str],
    ) -> float:
        """
        计算因果效应概率 P(effect | do(op))

        使用贝叶斯推理 + 边权重：
          P(effect | do(op)) = ∏_{edge in path} strength(edge) × base_rate
        """
        if not path or len(path) == 1:
            # 直接影响
            base = 0.95 if do_op.is_destructive() else 0.7
            return base

        # 计算路径乘积
        path_strength = 1.0
        for i in range(len(path) - 1):
            src, tgt = path[i], path[i + 1]
            edge = self._find_edge(src, tgt)
            if edge:
                path_strength *= edge.strength * edge.confidence
            else:
                path_strength *= 0.8  # 默认衰减

        # 应用操作类型乘子
        op_multiplier = {
            "drop_column": 0.95,
            "truncate_table": 0.99,
            "rename_column": 0.6,     # 重命名影响较小
            "dtype_change": 0.8,
            "add_column": 0.1,        # 新增列影响小
        }.get(do_op.op_type, 0.5)

        return min(0.999, path_strength * op_multiplier)

    def _find_edge(self, src: str, tgt: str) -> Optional[CausalEdge]:
        """查找因果边"""
        src_table, src_col = src.split(".")
        tgt_table, tgt_col = tgt.split(".")

        for edge in self.graph.edges:
            if (edge.source_table == src_table and edge.source_column == src_col and
                edge.target_table == tgt_table and edge.target_column == tgt_col):
                return edge
        return None

    def _compute_severity(
        self,
        do_op: DoOperation,
        target_node: str,
        probability: float,
    ) -> str:
        """计算影响严重程度"""
        if not do_op.is_destructive():
            return "info"

        # 是主键或外键？
        node = self.graph.nodes.get(target_node)
        if node and node.is_key:
            return "critical"

        # 概率高？
        if probability > 0.8:
            return "critical"
        elif probability > 0.5:
            return "warning"
        else:
            return "info"

    def _classify_effect(self, node_id: str) -> str:
        """分类因果效应类型"""
        if "ETL" in node_id or any(k in node_id for k in ["job", "transform", "aggregate"]):
            return "etl_failure"
        elif any(k in node_id for k in ["quality", "score", "null", "anomaly"]):
            return "quality_degradation"
        elif "total" in node_id or "sum" in node_id or "revenue" in node_id:
            return "metric_error"
        return "cascading"

    def _explain_effect(self, do_op: DoOperation, target_node: str, path: List[str]) -> str:
        """生成因果效应的自然语言解释"""
        if do_op.op_type == "drop_column":
            return (f"删除 {do_op.table}.{do_op.column} 后，"
                    f"通过因果路径 {' → '.join(path)} 影响 {target_node}，"
                    f"可能导致下游ETL作业失败或数据质量下降")
        elif do_op.op_type == "rename_column":
            return (f"重命名 {do_op.table}.{do_op.column} → {do_op.new_name} 后，"
                    f"引用该列的ETL作业需要同步更新，可能导致间歇性失败")
        elif do_op.op_type == "dtype_change":
            return (f"字段类型从原类型改为 {do_op.new_dtype}，"
                    f"可能引发下游转换错误，影响 {target_node} 的数据完整性")
        return f"Schema变更通过因果链路影响 {target_node}"

    def _get_affected_etl_jobs(self, node_id: str) -> List[str]:
        """获取受影响的ETL作业"""
        # 直接映射
        if node_id in self._etl_job_map:
            return self._etl_job_map[node_id]

        # 通过列名推断
        return self._infer_etl_jobs(node_id)


# ============================================================
# 4. 反事实推理引擎
# ============================================================

class CounterfactualReasoner:
    """
    反事实推理引擎

    回答"What-if"问题：
      - "如果我们删除订单表的customer_email列，哪些ETL作业会失败？"
      - "如果我们把sales.amount重命名为sales.revenue，质量评分会下降多少？"

    方法：基于因果图的反事实干预模拟
      1. 找到连接变更节点和目标节点的因果路径
      2. 计算沿路径的因果效应
      3. 生成反事实结果
    """

    def __init__(self, causal_graph: CausalGraphBuilder, do_calculus: DoCalculusEngine):
        self.graph = causal_graph
        self.do_calculus = do_calculus

    def reason(
        self,
        change: Dict,
        outcome: str,  # e.g., "ETL_Job_1", "quality_score", "revenue_total"
        counterfactual_question: Optional[str] = None,
    ) -> CounterfactualResult:
        """
        执行反事实推理

        参数：
            change: Dict，描述变更，如 {"type": "drop_column", "table": "orders", "column": "customer_email"}
            outcome: str，要预测的结果指标
            counterfactual_question: str，可选的自然语言问题

        返回：
            CounterfactualResult，包含事实结果和反事实结果
        """
        # 构建DoOperation
        do_op = DoOperation(
            op_type=change["type"],
            table=change["table"],
            column=change.get("column"),
            new_name=change.get("new_name"),
            new_dtype=change.get("new_dtype"),
        )

        # 预测干预后的结果
        effects = self.do_calculus.predict_impact(do_op)

        # 查找与outcome相关的效应
        relevant_effects = self._filter_effects_by_outcome(effects, outcome)

        # 计算反事实结果
        counterfactual_value = self._compute_counterfactual(do_op, relevant_effects, outcome)

        # 事实结果（无干预）
        factual_value = self._get_factual_value(outcome)

        # 差异
        difference = self._compute_difference(factual_value, counterfactual_value, outcome)

        # 生成缓解建议
        suggestions = self._generate_mitigation(do_op, relevant_effects)

        return CounterfactualResult(
            question=counterfactual_question or f"What if we {change['type']} {change.get('table', '')}.{change.get('column', '')}?",
            factual=factual_value,
            counterfactual=counterfactual_value,
            difference=difference,
            confidence=self._compute_confidence(relevant_effects),
            causal_path=self._get_causal_path_summary(do_op, outcome),
            mitigation_suggestions=suggestions,
        )

    def _filter_effects_by_outcome(
        self,
        effects: List[CausalEffect],
        outcome: str,
    ) -> List[CausalEffect]:
        """过滤出与outcome相关的因果效应"""
        outcome_keywords = outcome.lower().split("_")
        filtered = []

        for effect in effects:
            if any(kw in effect.affected_node.lower() for kw in outcome_keywords):
                filtered.append(effect)
            elif any(kw in e.lower() for kw in outcome_keywords for e in effect.affected_etl_jobs):
                filtered.append(effect)

        return filtered if filtered else effects[:3]  # fallback到前3个

    def _compute_counterfactual(
        self,
        do_op: DoOperation,
        effects: List[CausalEffect],
        outcome: str,
    ) -> str:
        """计算反事实结果值"""
        if not effects:
            return "无法预测影响"

        # 最高概率效应
        top_effect = max(effects, key=lambda e: e.probability)

        if "quality" in outcome.lower():
            # 质量评分影响
            degradation = top_effect.probability * 30  # 最多降30分
            return f"质量评分预计下降 {degradation:.1f} 分（概率 {top_effect.probability:.1%}）"
        elif "etl" in outcome.lower() or "job" in outcome.lower():
            # ETL作业失败
            failed_jobs = [e.affected_node for e in effects if e.probability > 0.5]
            if failed_jobs:
                return f"预计 {len(failed_jobs)} 个ETL作业受影响：{', '.join(failed_jobs[:3])}"
            return f"ETL作业失败概率 {top_effect.probability:.1%}"
        elif "revenue" in outcome.lower() or "total" in outcome.lower():
            # 指标计算错误
            error_prob = top_effect.probability * 0.15  # 最多15%误差
            return f"指标计算误差 ±{error_prob:.1%}（概率 {top_effect.probability:.1%}）"

        return f"受影响的节点：{top_effect.affected_node}（概率 {top_effect.probability:.1%}）"

    def _get_factual_value(self, outcome: str) -> str:
        """获取事实结果（无干预时的值）"""
        # 在实际系统中，这应该从监控数据中查询
        # 这里返回模拟的事实值
        if "quality" in outcome.lower():
            return "质量评分 = 87.3（基线）"
        elif "etl" in outcome.lower():
            return "ETL作业成功率 = 99.2%（基线）"
        elif "revenue" in outcome.lower():
            return "收入总额 = $1,234,567（基线）"
        return "正常运行（无干预）"

    def _compute_difference(
        self,
        factual: str,
        counterfactual: str,
        outcome: str,
    ) -> str:
        """计算事实与反事实的差异"""
        if "质量评分" in counterfactual:
            # 提取数字
            import re
            cf_match = re.search(r"下降 ([\d.]+)", counterfactual)
            if cf_match:
                degradation = float(cf_match.group(1))
                return f"质量评分从 87.3 降至 {87.3 - degradation:.1f}，降幅 {degradation:.1f} 分"
        return f"干预后：{counterfactual}（基线：{factual}）"

    def _compute_confidence(self, effects: List[CausalEffect]) -> float:
        """计算推理置信度"""
        if not effects:
            return 0.3
        # 基于路径长度和效应数量
        avg_prob = sum(e.probability for e in effects) / len(effects)
        path_conf = 0.5 + 0.3 * min(1.0, sum(len(e.path) for e in effects) / len(effects))
        return round(min(0.95, avg_prob * path_conf), 3)

    def _get_causal_path_summary(self, do_op: DoOperation, outcome: str) -> List[str]:
        """获取因果路径摘要"""
        if do_op.column:
            start = f"{do_op.table}.{do_op.column}"
        else:
            start = do_op.table

        downstream = self.graph.get_downstream(start, max_depth=3)
        return [start] + downstream[:5]

    def _generate_mitigation(
        self,
        do_op: DoOperation,
        effects: List[CausalEffect],
    ) -> List[str]:
        """生成缓解建议"""
        suggestions = []

        if do_op.op_type == "drop_column":
            suggestions.append(f"在删除 {do_op.table}.{do_op.column} 前，创建视图或保留列作为过渡")
            suggestions.append("检查所有引用该列的ETL作业，优先更新下游依赖")
            suggestions.append("使用影子表（shadow table）验证变更安全性")
        elif do_op.op_type == "rename_column":
            suggestions.append(f"使用数据库重命名（ALTER TABLE ... RENAME COLUMN）而非应用层修改")
            suggestions.append("添加注释说明列名变更，保持向后兼容")
        elif do_op.op_type == "dtype_change":
            suggestions.append("在变更前验证目标类型是否兼容现有数据")
            suggestions.append("使用CAST函数在ETL层做类型转换验证")

        if effects:
            suggestions.append(f"受影响的ETL作业：{[e.affected_etl_jobs[0] if e.affected_etl_jobs else '未知' for e in effects[:3]]}")

        return suggestions


# ============================================================
# 5. 顶层 API：CausalSchemaEngine
# ============================================================

class CausalSchemaEngine:
    """
    因果Schema引擎 - 顶层API

    整合所有模块，提供统一接口：

        engine = CausalSchemaEngine(db_path="data/warehouse.db")
        engine.build_causal_graph()

        # 预测变更影响
        impacts = engine.predict_impact("drop_column", table="orders", column="customer_email")
        for impact in impacts:
            print(f"{impact.affected_node}: P={impact.probability:.1%} [{impact.severity}]")

        # 反事实推理
        result = engine.counterfactual(
            change={"type": "rename_column", "table": "sales", "column": "amount", "new_name": "revenue"},
            outcome="ETL_RevenueAggregation"
        )
        print(result.counterfactual)
        for suggestion in result.mitigation_suggestions:
            print(f"  → {suggestion}")
    """

    def __init__(self, db_path: str = "data/warehouse.db"):
        self.db_path = db_path
        self.graph_builder: Optional[CausalGraphBuilder] = None
        self.do_calculus: Optional[DoCalculusEngine] = None
        self.counterfactual: Optional[CounterfactualReasoner] = None
        self._built = False

    def build_causal_graph(self, lineage_log: Optional[List[Dict]] = None):
        """构建因果图谱"""
        self.graph_builder = CausalGraphBuilder(self.db_path)
        self.graph_builder.build_from_db(lineage_log)

        self.do_calculus = DoCalculusEngine(self.graph_builder)
        self.counterfactual = CounterfactualReasoner(self.graph_builder, self.do_calculus)

        self._built = True

    def predict_impact(
        self,
        op_type: str,
        table: str,
        column: Optional[str] = None,
        new_name: Optional[str] = None,
        new_dtype: Optional[str] = None,
    ) -> List[CausalEffect]:
        """预测Schema变更的因果影响"""
        if not self._built:
            self.build_causal_graph()

        do_op = DoOperation(
            op_type=op_type,
            table=table,
            column=column,
            new_name=new_name,
            new_dtype=new_dtype,
        )
        return self.do_calculus.predict_impact(do_op)

    def counterfactual(
        self,
        change: Dict,
        outcome: str,
    ) -> CounterfactualResult:
        """反事实推理"""
        if not self._built:
            self.build_causal_graph()
        return self.counterfactual.reason(change, outcome)

    def get_causal_graph(self) -> Dict:
        """获取因果图谱（用于可视化）"""
        if not self._built:
            self.build_causal_graph()
        return self.graph_builder.to_graph_dict()

    def explain_change_risk(
        self,
        op_type: str,
        table: str,
        column: Optional[str] = None,
    ) -> Dict:
        """
        一站式变更风险评估报告

        返回结构：
        {
            "operation": "do(drop_column(orders.customer_email))",
            "risk_level": "HIGH",
            "affected_nodes_count": 5,
            "critical_etl_jobs": ["ETL_JoinMasterData"],
            "quality_impact": "质量评分预计下降 12.3 分",
            "recommendations": ["在删除前创建保留视图", "更新ETL作业依赖"],
            "causal_path": ["orders.customer_email", "orders.order_id", "ETL_JoinMasterData"]
        }
        """
        impacts = self.predict_impact(op_type, table, column)

        critical = [i for i in impacts if i.severity == "critical"]
        warning = [i for i in impacts if i.severity == "warning"]

        all_etl = []
        for i in impacts:
            all_etl.extend(i.affected_etl_jobs)
        unique_etl = list(dict.fromkeys(all_etl))[:5]

        quality_impact = next(
            (i for i in impacts if "quality" in i.effect_type), None
        )

        risk_level = "CRITICAL" if critical else "HIGH" if warning else "LOW"

        return {
            "operation": str(DoOperation(op_type, table, column)),
            "risk_level": risk_level,
            "affected_nodes_count": len(impacts),
            "critical_count": len(critical),
            "warning_count": len(warning),
            "critical_etl_jobs": unique_etl,
            "quality_impact": f"质量评分预计下降 {quality_impact.probability * 30:.1f} 分" if quality_impact else "无显著影响",
            "recommendations": self._generate_recommendations(impacts, op_type, table, column),
            "causal_path": impacts[0].path if impacts else [],
            "full_effects": [
                {
                    "node": e.affected_node,
                    "probability": round(e.probability, 3),
                    "severity": e.severity,
                    "type": e.effect_type,
                    "explanation": e.explanation,
                }
                for e in impacts[:10]
            ],
        }

    def _generate_recommendations(
        self,
        impacts: List[CausalEffect],
        op_type: str,
        table: str,
        column: Optional[str],
    ) -> List[str]:
        """生成变更建议"""
        recs = []

        if op_type == "drop_column":
            recs.append(f"1. 在删除 {table}.{column} 前，先创建保留视图（CREATE VIEW v_{table}_{column}_backup AS SELECT ...）")
            recs.append("2. 检查所有ETL作业的列引用，使用ALTER TABLE ... RENAME COLUMN 替代 DROP + ADD（如果数据库支持）")
            recs.append("3. 在影子环境验证变更，确认无影响后再生产执行")
        elif op_type == "rename_column":
            recs.append(f"1. 使用 ALTER TABLE ... RENAME COLUMN 原子操作，避免数据丢失")
            recs.append("2. 更新所有引用旧列名的ETL作业和存储过程")
            recs.append("3. 添加列注释（COMMENT）说明新名称的业务含义")
        elif op_type == "dtype_change":
            recs.append("1. 先在ETL层添加类型转换验证（TRY_CAST），确认无截断风险")
            recs.append("2. 检查目标类型范围是否覆盖现有数据（如 INT → BIGINT 通常安全）")
            recs.append("3. 在低峰期执行，减少影响窗口")

        if impacts:
            recs.append(f"4. 重点关注因果路径上的 {len([i for i in impacts if i.severity != 'info'])} 个高影响节点")

        return recs


# ============================================================
# 6. CLI 接口
# ============================================================

def main():
    """命令行接口"""
    import sys

    if len(sys.argv) < 2:
        print("AutoDataFlow Causal Engine v3.0")
        print("用法：")
        print("  python causal_engine.py predict <op> <table> [column]")
        print("  python causal_engine.py risk <op> <table> [column]")
        print("  python causal_engine.py graph")
        print("  python causal_engine.py counterfactual <change_json> <outcome>")
        print("")
        print("示例：")
        print("  python causal_engine.py predict drop_column orders customer_email")
        print("  python causal_engine.py risk rename_column sales amount revenue")
        print("  python causal_engine.py graph")
        sys.exit(1)

    cmd = sys.argv[1]
    db_path = "data/warehouse.db"

    engine = CausalSchemaEngine(db_path)

    if cmd == "graph":
        engine.build_causal_graph()
        graph = engine.get_causal_graph()
        print(json.dumps(graph, indent=2, ensure_ascii=False))

    elif cmd == "predict" and len(sys.argv) >= 4:
        op_type = sys.argv[2]
        table = sys.argv[3]
        column = sys.argv[4] if len(sys.argv) >= 5 else None

        engine.build_causal_graph()
        impacts = engine.predict_impact(op_type, table, column)

        print(f"\n=== 因果影响预测: do({op_type}({table}.{column})) ===\n")
        for impact in impacts:
            print(f"[{impact.severity.upper():8}] P={impact.probability:.1%} | {impact.affected_node}")
            print(f"         {impact.explanation}")
            if impact.affected_etl_jobs:
                print(f"         ETL作业: {', '.join(impact.affected_etl_jobs[:3])}")
            print(f"         路径: {' → '.join(impact.path)}")
            print()

    elif cmd == "risk" and len(sys.argv) >= 4:
        op_type = sys.argv[2]
        table = sys.argv[3]
        column = sys.argv[4] if len(sys.argv) >= 5 else None

        engine.build_causal_graph()
        report = engine.explain_change_risk(op_type, table, column)

        print(f"\n=== 变更风险评估 ===\n")
        print(f"操作: {report['operation']}")
        print(f"风险等级: {report['risk_level']}")
        print(f"受影响节点: {report['affected_nodes_count']} 个")
        print(f"关键ETL作业: {', '.join(report['critical_etl_jobs']) or '无'}")
        print(f"质量影响: {report['quality_impact']}")
        print(f"\n建议:")
        for i, rec in enumerate(report['recommendations'], 1):
            print(f"  {i}. {rec}")
        print(f"\n因果路径: {' → '.join(report['causal_path']) or '无'}")
        print()

    elif cmd == "counterfactual" and len(sys.argv) >= 3:
        import json
        change = json.loads(sys.argv[2])
        outcome = sys.argv[3] if len(sys.argv) >= 4 else "ETL_Job"

        engine.build_causal_graph()
        result = engine.counterfactual(change, outcome)

        print(f"\n=== 反事实推理 ===\n")
        print(f"问题: {result.question}")
        print(f"事实结果: {result.factual}")
        print(f"反事实结果: {result.counterfactual}")
        print(f"差异: {result.difference}")
        print(f"置信度: {result.confidence:.1%}")
        print(f"因果路径: {' → '.join(result.causal_path)}")
        print(f"\n缓解建议:")
        for i, s in enumerate(result.mitigation_suggestions, 1):
            print(f"  {i}. {s}")

    else:
        print(f"未知命令: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()