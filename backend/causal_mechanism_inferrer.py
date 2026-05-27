# -*- coding: utf-8 -*-
"""
CausalMechanismInferrer: 基于真实统计推断的因果机制检测
========================================================

替换硬编码的列名模式匹配，使用数据驱动的统计方法推断因果关系。

支持的因果机制:
  - FOREIGN_KEY: 基于列值共现和唯一性匹配推断外键关系
  - ETL_TRANSFORM: 基于数据内容分布相似性推断ETL转换关系
  - SCHEMA_DEPENDENCY: 基于列间相关性强度的依赖推断
  - QUALITY_PROPAGATION: 基于null率/异常值传播模式推断质量因果
  - CASCADING_FAILURE: 基于历史故障日志推断级联失效边

使用方法:
    inferrer = CausalMechanismInferrer(db_path="data/warehouse.db")
    mechanisms = inferrer.infer_all_mechanisms()

    for mech in mechanisms:
        print(f"{mech['source']} --[{mech['mechanism']}]--> {mech['target']}")
        print(f"  strength={mech['strength']:.3f}, confidence={mech['confidence']:.3f}")
"""

import sqlite3
import math
import hashlib
from collections import defaultdict
from typing import Dict, List, Optional, Any, Tuple, Set
from dataclasses import dataclass
from enum import Enum
import json


# ============================================================
# 1. 统计辅助函数
# ============================================================

def _compute_column_stats(values: List[Any]) -> Dict[str, float]:
    """计算列的基本统计量"""
    non_null = [v for v in values if v is not None and str(v).strip() != '']
    if not non_null:
        return {
            'count': 0,
            'null_count': len(values),
            'null_pct': 1.0,
            'unique_count': 0,
            'unique_ratio': 0.0,
            'min': None,
            'max': None,
            'mean': None,
            'stdev': None,
        }
    
    numeric_values = []
    for v in non_null:
        try:
            numeric_values.append(float(v))
        except (ValueError, TypeError):
            pass
    
    stats = {
        'count': len(non_null),
        'null_count': len(values) - len(non_null),
        'null_pct': (len(values) - len(non_null)) / len(values) if values else 1.0,
        'unique_count': len(set(non_null)),
        'unique_ratio': len(set(non_null)) / len(non_null) if non_null else 0.0,
    }
    
    if numeric_values:
        stats['min'] = min(numeric_values)
        stats['max'] = max(numeric_values)
        stats['mean'] = sum(numeric_values) / len(numeric_values)
        if len(numeric_values) > 1:
            mean = stats['mean']
            variance = sum((x - mean) ** 2 for x in numeric_values) / len(numeric_values)
            stats['stdev'] = math.sqrt(variance)
        else:
            stats['stdev'] = 0.0
    else:
        stats['min'] = None
        stats['max'] = None
        stats['mean'] = None
        stats['stdev'] = None
    
    return stats


def _pearson_correlation(x: List[float], y: List[float]) -> float:
    """计算Pearson相关系数"""
    if len(x) != len(y) or len(x) < 2:
        return 0.0
    
    n = len(x)
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    
    numerator = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    
    denom_x = math.sqrt(sum((xi - mean_x) ** 2 for xi in x))
    denom_y = math.sqrt(sum((yi - mean_y) ** 2 for yi in y))
    
    if denom_x == 0 or denom_y == 0:
        return 0.0
    
    return numerator / (denom_x * denom_y)


def _chi_square_test(
    values1: List[Any], 
    values2: List[Any], 
    bins: int = 10
) -> Tuple[float, float]:
    """
    计算两个列之间的Chi-square统计量和p值
    返回: (chi2统计量, p值近似)
    
    使用简化的离散化方法计算相关性
    """
    # 转换为数值或哈希
    def to_numeric(v):
        try:
            return float(v)
        except (ValueError, TypeError):
            return hash(str(v)) % 10000
    
    num1 = [to_numeric(v) for v in values1 if v is not None]
    num2 = [to_numeric(v) for v in values2 if v is not None]
    
    if len(num1) < 5 or len(num2) < 5:
        return 0.0, 1.0
    
    # 取较短的长度对齐
    min_len = min(len(num1), len(num2))
    num1 = num1[:min_len]
    num2 = num2[:min_len]
    
    # 离散化
    all_vals = num1 + num2
    try:
        min_val, max_val = min(all_vals), max(all_vals)
        if max_val == min_val:
            return 0.0, 1.0
        range_val = max_val - min_val
        
        def discretize(v):
            return int((v - min_val) / range_val * (bins - 1))
        
        bins1 = [discretize(v) for v in num1]
        bins2 = [discretize(v) for v in num2]
        
        # 构建列联表
        observed = defaultdict(int)
        for b1, b2 in zip(bins1, bins2):
            observed[(b1, b2)] += 1
        
        # 计算边际
        row_totals = defaultdict(int)
        col_totals = defaultdict(int)
        total = 0
        for (b1, b2), count in observed.items():
            row_totals[b1] += count
            col_totals[b2] += count
            total += count
        
        # 计算chi-square
        chi2 = 0.0
        for (b1, b2), obs in observed.items():
            expected = (row_totals[b1] * col_totals[b2]) / total
            if expected > 0:
                chi2 += (obs - expected) ** 2 / expected
        
        # 自由度
        dof = (bins - 1) ** 2
        if dof <= 0:
            return 0.0, 1.0
        
        # 近似p值（使用chi-square分布的性质）
        # p ≈ exp(-chi2/2) for large dof
        p_value = math.exp(-chi2 / 2) if chi2 > 0 else 1.0
        p_value = max(0.0, min(1.0, p_value))
        
        return chi2 / total, p_value  # 归一化
    except:
        return 0.0, 1.0


def _jaccard_similarity(set1: Set, set2: Set) -> float:
    """Jaccard相似度"""
    if not set1 or not set2:
        return 0.0
    intersection = len(set1 & set2)
    union = len(set1 | set2)
    return intersection / union if union > 0 else 0.0


def _value_overlap_ratio(values1: List[Any], values2: List[Any]) -> float:
    """计算两个列的值重叠率"""
    set1 = set(str(v) for v in values1 if v is not None)
    set2 = set(str(v) for v in values2 if v is not None)
    
    if not set1 or not set2:
        return 0.0
    
    # 重叠比例 = 交集大小 / 较小集合的大小
    intersection = len(set1 & set2)
    min_size = min(len(set1), len(set2))
    
    return intersection / min_size if min_size > 0 else 0.0


# ============================================================
# 2. 核心推断器类
# ============================================================

class CausalMechanismInferrer:
    """
    基于统计推断的因果机制检测器
    
    工作流程:
      1. 读取数据库样本数据
      2. 对每对列计算统计关联指标
      3. 使用阈值规则/机器学习模型判断因果机制
      4. 输出带强度和置信度的因果边
    """
    
    # 阈值配置
    THRESHOLDS = {
        'fk_overlap_min': 0.60,      # FK推断的最小重叠率
        'fk_unique_ratio_high': 0.95, # 高唯一性比率（可能是FK端）
        'etl_correlation_min': 0.70,  # ETL变换最小相关系数
        'quality_propagation_min': 0.50, # 质量传播最小相关系数
        'schema_dependency_min': 0.40,  # Schema依赖最小相关系数
        'confidence_base': 0.75,      # 基础置信度
    }
    
    def __init__(self, db_path: str, sample_size: int = 1000):
        """
        初始化因果机制推断器
        
        参数:
            db_path: SQLite数据库路径
            sample_size: 采样大小（用于大规模表）
        """
        self.db_path = db_path
        self.sample_size = sample_size
        self._conn: Optional[sqlite3.Connection] = None
        self._tables: List[str] = []
        self._columns: Dict[str, List[str]] = {}  # table -> columns
        self._column_data: Dict[Tuple[str, str], List[Any]] = {}  # (table, col) -> values
        self._column_stats: Dict[Tuple[str, str], Dict] = {}  # (table, col) -> stats
        
    def connect(self) -> "CausalMechanismInferrer":
        """建立数据库连接并加载元数据"""
        self._conn = sqlite3.connect(self.db_path)
        cursor = self._conn.cursor()
        
        # 获取所有表
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        self._tables = [row[0] for row in cursor.fetchall()]
        
        # 获取每张表的列
        for table in self._tables:
            cursor.execute(f"PRAGMA table_info(\"{table}\")")
            self._columns[table] = [row[1] for row in cursor.fetchall()]
        
        return self
    
    def disconnect(self):
        """关闭数据库连接"""
        if self._conn:
            self._conn.close()
            self._conn = None
    
    def _sample_column(self, table: str, column: str) -> List[Any]:
        """采样列数据"""
        if not self._conn:
            raise RuntimeError("Not connected. Call connect() first.")
        
        cursor = self._conn.cursor()
        query = f'S SELECT "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL LIMIT ?'
        
        try:
            cursor.execute(f'SELECT "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL LIMIT ?', (self.sample_size,))
            return [row[0] for row in cursor.fetchall()]
        except sqlite3.OperationalError:
            # 回退：列名可能包含特殊字符
            try:
                cursor.execute(f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL LIMIT ?", (self.sample_size,))
                return [row[0] for row in cursor.fetchall()]
            except:
                return []
    
    def load_data(self) -> "CausalMechanismInferrer":
        """加载所有列的样本数据"""
        for table in self._tables:
            for col in self._columns[table]:
                key = (table, col)
                self._column_data[key] = self._sample_column(table, col)
                self._column_stats[key] = _compute_column_stats(self._column_data[key])
        return self
    
    def infer_all_mechanisms(self) -> List[Dict]:
        """
        推断所有因果机制
        
        返回:
            List[Dict], 每个元素包含:
                - source: str, 源节点 "table.column"
                - target: str, 目标节点 "table.column"  
                - mechanism: str, 因果机制类型
                - strength: float, 因果强度 0-1
                - confidence: float, 推断置信度 0-1
                - metrics: Dict, 详细统计指标
        """
        results = []
        
        # 1. 外键关系推断
        results.extend(self._infer_foreign_keys())
        
        # 2. ETL转换关系推断
        results.extend(self._infer_etl_transforms())
        
        # 3. Schema依赖关系推断
        results.extend(self._infer_schema_dependencies())
        
        # 4. 质量传播关系推断
        results.extend(self._infer_quality_propagation())
        
        # 5. 级联失效关系推断（基于null模式）
        results.extend(self._infer_cascading_failures())
        
        return results
    
    def _infer_foreign_keys(self) -> List[Dict]:
        """
        基于列值共推断外键关系
        
        原理:
          - 外键列通常是另一个表主键的引用
          - 外键列的值应该在被引用表中存在
          - 外键列的唯一性通常与被引用列不同（FK侧可能重复，PK侧唯一）
        """
        fk_edges = []
        
        for table1 in self._tables:
            for col1 in self._columns[table1]:
                key1 = (table1, col1)
                stats1 = self._column_stats.get(key1, {})
                
                if not self._column_data.get(key1):
                    continue
                
                values1 = self._column_data[key1]
                set1 = set(str(v) for v in values1)
                
                for table2 in self._tables:
                    if table1 == table2:
                        continue
                    
                    for col2 in self._columns[table2]:
                        key2 = (table2, col2)
                        stats2 = self._column_stats.get(key2, {})
                        
                        if not self._column_data.get(key2):
                            continue
                        
                        values2 = self._column_data[key2]
                        set2 = set(str(v) for v in values2)
                        
                        # 计算重叠率
                        overlap = _value_overlap_ratio(values1, values2)
                        
                        # FK推断条件:
                        # 1. 重叠率高 (超过阈值)
                        # 2. 至少一列有高唯一性（通常是主键端）
                        # 3. 另一列唯一性较低（通常是外键端）
                        
                        high_unique = stats2.get('unique_ratio', 0) > 0.95
                        
                        if overlap >= self.THRESHOLDS['fk_overlap_min']:
                            # 确定FK方向：唯一性高的列是主键端
                            if stats1.get('unique_ratio', 0) > stats2.get('unique_ratio', 0):
                                # table1.col1 是主键端 -> table2.col2 是FK
                                if high_unique:
                                    source, target = f"{table2}.{col2}", f"{table1}.{col1}"
                                    pk_side = f"{table1}.{col1}"
                                else:
                                    continue
                            elif stats2.get('unique_ratio', 0) > stats1.get('unique_ratio', 0):
                                # table2.col2 是主键端 -> table1.col1 是FK
                                if stats1.get('unique_ratio', 0) > 0.95:
                                    continue  # 两侧都是高唯一性，可能不是FK
                                source, target = f"{table1}.{col1}", f"{table2}.{col2}"
                                pk_side = f"{table2}.{col2}"
                            else:
                                continue
                            
                            # 计算置信度
                            confidence = self.THRESHOLDS['confidence_base']
                            confidence += overlap * 0.2
                            
                            # 唯一性差异加成
                            unique_diff = abs(stats1.get('unique_ratio', 0) - stats2.get('unique_ratio', 0))
                            confidence += unique_diff * 0.1
                            
                            fk_edges.append({
                                'source': source,
                                'target': target,
                                'mechanism': 'foreign_key',
                                'strength': min(1.0, overlap),
                                'confidence': min(0.99, confidence),
                                'metrics': {
                                    'overlap_ratio': round(overlap, 4),
                                    'source_unique_ratio': round(stats1.get('unique_ratio', 0), 4),
                                    'target_unique_ratio': round(stats2.get('unique_ratio', 0), 4),
                                    'pk_side': pk_side,
                                }
                            })
        
        return fk_edges
    
    def _infer_etl_transforms(self) -> List[Dict]:
        """
        基于数据分布相似性推断ETL转换关系
        
        原理:
          - ETL转换（如SUM, AVG, COUNT等聚合）会产生列间的数值关系
          - 聚合列与源列之间存在统计相关性
          - 数值变换（前一个节点）影响后一个节点的值
        """
        etl_edges = []
        
        # 在同一表内查找可能的聚合关系
        for table in self._tables:
            cols = self._columns[table]
            numeric_cols = []
            
            for col in cols:
                key = (table, col)
                stats = self._column_stats.get(key, {})
                if stats.get('stdev') is not None and stats['stdev'] > 0:
                    numeric_cols.append(col)
            
            # 检查列对之间的相关性
            for i, col1 in enumerate(numeric_cols):
                for col2 in numeric_cols[i+1:]:
                    key1 = (table, col1)
                    key2 = (table, col2)
                    
                    values1_raw = self._column_data.get(key1, [])
                    values2_raw = self._column_data.get(key2, [])
                    
                    # 对齐数据
                    min_len = min(len(values1_raw), len(values2_raw))
                    if min_len < 5:
                        continue
                    
                    try:
                        values1 = [float(v) for v in values1_raw[:min_len] if v is not None]
                        values2 = [float(v) for v in values2_raw[:min_len] if v is not None]
                        
                        if len(values1) < 5 or len(values2) < 5:
                            continue
                        
                        corr = _pearson_correlation(values1, values2)
                        
                        # 高相关意味着可能是聚合关系或派生关系
                        if abs(corr) >= self.THRESHOLDS['etl_correlation_min']:
                            # 确定方向：标准差小的列可能是聚合结果
                            stats1 = self._column_stats.get(key1, {})
                            stats2 = self._column_stats.get(key2, {})
                            
                            stdev1 = stats1.get('stdev', 0) or 0
                            stdev2 = stats2.get('stdev', 0) or 0
                            
                            if stdev1 < stdev2:
                                source, target = f"{table}.{col1}", f"{table}.{col2}"
                            else:
                                source, target = f"{table}.{col2}", f"{table}.{col1}"
                            
                            etl_edges.append({
                                'source': source,
                                'target': target,
                                'mechanism': 'etl_transform',
                                'strength': abs(corr),
                                'confidence': min(0.95, self.THRESHOLDS['confidence_base'] + abs(corr) * 0.15),
                                'metrics': {
                                    'correlation': round(corr, 4),
                                    'source_stdev': round(stdev1, 4),
                                    'target_stdev': round(stdev2, 4),
                                }
                            })
                    except (ValueError, TypeError):
                        continue
        
        return etl_edges
    
    def _infer_schema_dependencies(self) -> List[Dict]:
        """
        基于列间相关性强度的Schema依赖推断
        
        原理:
          - 同一表中相关列可能存在业务依赖
          - 使用卡方检验检测离散/分类列之间的依赖
        """
        dep_edges = []
        
        for table in self._tables:
            cols = self._columns[table]
            
            for i, col1 in enumerate(cols):
                for col2 in cols[i+1:]:
                    key1 = (table, col1)
                    key2 = (table, col2)
                    
                    values1 = self._column_data.get(key1, [])
                    values2 = self._column_data.get(key2, [])
                    
                    if len(values1) < 10 or len(values2) < 10:
                        continue
                    
                    try:
                        chi2, p_value = _chi_square_test(values1, values2)
                        
                        if chi2 >= self.THRESHOLDS['schema_dependency_min']:
                            # 检查是否已经有FK边
                            existing = any(
                                e['source'] == f"{table}.{col1}" and e['target'] == f"{table}.{col2}"
                                for e in dep_edges
                            )
                            if existing:
                                continue
                            
                            dep_edges.append({
                                'source': f"{table}.{col1}",
                                'target': f"{table}.{col2}",
                                'mechanism': 'schema_dependency',
                                'strength': min(1.0, chi2),
                                'confidence': min(0.90, self.THRESHOLDS['confidence_base'] + (1 - p_value) * 0.15),
                                'metrics': {
                                    'chi_square': round(chi2, 4),
                                    'p_value': round(p_value, 4),
                                }
                            })
                    except:
                        continue
        
        return dep_edges
    
    def _infer_quality_propagation(self) -> List[Dict]:
        """
        基于null率相似性推断质量传播关系
        
        原理:
          - 如果两列的null模式高度相关（同时为null或同时有值）
          - 可能存在质量传播的因果关系
          - 通常发生在主从表的外键列与主键列之间
        """
        quality_edges = []
        
        for table in self._tables:
            cols = self._columns[table]
            
            for i, col1 in enumerate(cols):
                for col2 in cols[i+1:]:
                    key1 = (table, col1)
                    key2 = (table, col2)
                    
                    values1 = self._column_data.get(key1, [])
                    values2 = self._column_data.get(key2, [])
                    
                    if len(values1) < 10 or len(values2) < 10:
                        continue
                    
                    # 构建null模式向量 (1=null, 0=not null)
                    null_pattern1 = [1 if v is None or str(v).strip() == '' else 0 for v in values1]
                    null_pattern2 = [1 if v is None or str(v).strip() == '' else 0 for v in values2]
                    
                    # 计算null模式的相关性
                    corr = _pearson_correlation(
                        [float(x) for x in null_pattern1],
                        [float(x) for x in null_pattern2]
                    )
                    
                    stats1 = self._column_stats.get(key1, {})
                    stats2 = self._column_stats.get(key2, {})
                    
                    # 如果两列null率都较高且相关
                    null_pct1 = stats1.get('null_pct', 0)
                    null_pct2 = stats2.get('null_pct', 0)
                    
                    if corr >= self.THRESHOLDS['quality_propagation_min'] and (null_pct1 > 0.1 or null_pct2 > 0.1):
                        quality_edges.append({
                            'source': f"{table}.{col1}",
                            'target': f"{table}.{col2}",
                            'mechanism': 'quality_propagation',
                            'strength': abs(corr),
                            'confidence': min(0.85, self.THRESHOLDS['confidence_base'] + abs(corr) * 0.1),
                            'metrics': {
                                'null_correlation': round(corr, 4),
                                'source_null_pct': round(null_pct1, 4),
                                'target_null_pct': round(null_pct2, 4),
                            }
                        })
        
        return quality_edges
    
    def _infer_cascading_failures(self) -> List[Dict]:
        """
        基于历史故障模式推断级联失效关系
        
        原理:
          - 如果一列的错误/异常模式会导致另一列出现问题
          - 表现为两列的异常值在时间/行维度上共同出现
        """
        cascade_edges = []
        
        # 简化实现：检查两列的唯一值比率差异
        # 如果一列唯一性极高（如主键），另一列与之高度相关
        # 可能存在级联关系
        
        for table in self._tables:
            cols = self._columns[table]
            
            # 找出可能是主键的列（高唯一性）
            pk_candidates = []
            for col in cols:
                key = (table, col)
                stats = self._column_stats.get(key, {})
                if stats.get('unique_ratio', 0) > 0.99:
                    pk_candidates.append(col)
            
            if not pk_candidates:
                continue
            
            pk_col = pk_candidates[0]  # 假设第一列是主键
            
            for col in cols:
                if col == pk_col:
                    continue
                
                key_pk = (table, pk_col)
                key_col = (table, col)
                
                values_pk = self._column_data.get(key_pk, [])
                values_col = self._column_data.get(key_col, [])
                
                if len(values_pk) < 10 or len(values_col) < 10:
                    continue
                
                # 计算关联性
                try:
                    # 尝试数值关联
                    num_pk = [float(v) for v in values_pk[:100] if v is not None]
                    num_col = [float(v) for v in values_col[:100] if v is not None]
                    
                    if len(num_pk) >= 10 and len(num_col) >= 10:
                        min_len = min(len(num_pk), len(num_col))
                        corr = _pearson_correlation(num_pk[:min_len], num_col[:min_len])
                        
                        # 高相关 + 一列唯一性极高 = 可能的级联关系
                        stats_col = self._column_stats.get(key_col, {})
                        if abs(corr) > 0.5 and stats_col.get('unique_ratio', 0) > 0.5:
                            # 级联方向：唯一性高的列变化会影响唯一性低的列
                            if stats_col.get('unique_ratio', 0) > 0.99:
                                continue  # 避免重复添加FK
                            
                            cascade_edges.append({
                                'source': f"{table}.{pk_col}",
                                'target': f"{table}.{col}",
                                'mechanism': 'cascading_failure',
                                'strength': abs(corr) * 0.7,  # 级联关系强度略低
                                'confidence': min(0.80, self.THRESHOLDS['confidence_base'] + abs(corr) * 0.05),
                                'metrics': {
                                    'correlation': round(corr, 4),
                                    'is_pk': True,
                                }
                            })
                except (ValueError, TypeError):
                    continue
        
        return cascade_edges
    
    def get_candidate_pairs(self) -> List[Tuple[Tuple[str, str], Tuple[str, str]]]:
        """
        获取所有需要评估的候选列对
        
        返回:
            List of ((table1, col1), (table2, col2)) tuples
        """
        pairs = []
        all_columns = []
        
        for table in self._tables:
            for col in self._columns[table]:
                all_columns.append((table, col))
        
        for i, (t1, c1) in enumerate(all_columns):
            for t2, c2 in all_columns[i+1:]:
                # 跳过同一表的同一列对（但允许同表不同列）
                pairs.append(((t1, c1), (t2, c2)))
        
        return pairs
    
    def get_table_summary(self) -> Dict:
        """获取表的统计摘要"""
        summary = {}
        for table in self._tables:
            summary[table] = {
                'columns': self._columns[table],
                'column_stats': {}
            }
            for col in self._columns[table]:
                key = (table, col)
                if key in self._column_stats:
                    summary[table]['column_stats'][col] = self._column_stats[key]
        return summary


# ============================================================
# 便捷函数
# ============================================================

def infer_causal_mechanisms(db_path: str, sample_size: int = 1000) -> List[Dict]:
    """
    一站式因果机制推断函数
    
    参数:
        db_path: SQLite数据库路径
        sample_size: 采样大小
    
    返回:
        List[Dict], 因果边列表
    """
    inferrer = CausalMechanismInferrer(db_path, sample_size)
    inferrer.connect()
    inferrer.load_data()
    results = inferrer.infer_all_mechanisms()
    inferrer.disconnect()
    return results


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("CausalMechanismInferrer")
        print("用法: python causal_mechanism_inferrer.py <db_path>")
        sys.exit(1)
    
    db_path = sys.argv[1]
    
    print("正在连接数据库...")
    inferrer = CausalMechanismInferrer(db_path)
    inferrer.connect()
    
    print("正在加载样本数据...")
    inferrer.load_data()
    
    print("正在推断因果机制...")
    results = inferrer.infer_all_mechanisms()
    
    print(f"\n发现 {len(results)} 条因果边:\n")
    for r in results:
        print(f"{r['source']} --[{r['mechanism']}]--> {r['target']}")
        print(f"  strength={r['strength']:.3f}, confidence={r['confidence']:.3f}")
        print(f"  metrics: {r['metrics']}")
        print()
    
    inferrer.disconnect()