# AutoDataFlow v3.0 创新点技术文档

**版本**: v3.0 | **更新日期**: 2026-05-28 | **项目级别**: A1类

---

## 创新点总览

AutoDataFlow 从传统的"Schema变更检测工具"升级为**因果驱动的Schema演变预测框架**，核心创新涵盖四个维度。

| 编号 | 创新点 | 技术方法 | 学术价值 |
|------|--------|----------|----------|
| C1 | 因果血缘图谱 | CausalEdge(5种机制) + SchemaNode | 从相关性升级到因果性 |
| C2 | Do-Calculus变更预测 | P(Y\|do(X)) 干预推理 | 因果推断在数据治理中的首次应用 |
| C3 | 反事实质量推理 | factual/counterfactual/difference | 回答"如果变更某字段会怎样" |
| C4 | 多Agent因果协作 | Schema/ETL/Observer/Viz四Agent | 端到端自治数据治理 |

---

## C1: 因果血缘图谱 (Causal Lineage Graph)

### 问题背景

传统数据血缘(Data Lineage)仅记录数据的流向关系(A -> B)，属于**相关性**描述。当Schema发生变更时，无法量化"A的变化会在多大程度上导致B的变化"。

### 技术方案

引入**因果边(CausalEdge)**替代普通血缘边，每条边携带因果机制类型和强度参数。

#### 五种因果机制

```
1. FOREIGN_KEY (外键因果, strength=1.0)
   orders.customer_id -> customers.id
   确定性因果：删除customers.id必然导致orders查询失败

2. ETL_TRANSFORM (ETL变换因果, strength=0.6~0.9)
   raw_sales.amount -> daily_summary.total_revenue
   聚合因果：源列变更通过SUM/AVG等聚合传播

3. SCHEMA_DEPENDENCY (Schema依赖因果, strength=0.5~0.8)
   orders.status -> etl_report.status
   引用因果：下游视图/报表直接引用上游列

4. QUALITY_PROPAGATION (质量传播因果, strength=0.3~0.7)
   products.stock -> order_fulfillment.delivery_time
   间接因果：上游数据质量下降间接影响下游指标

5. CASCADING_FAILURE (级联故障因果, strength=0.4~0.8)
   payments.status -> order_fulfillment.shipped_at
   故障传播：上游故障沿依赖链传播
```

### 代码实现

- 核心模块: `backend/causal_engine.py` (1121行)
- 辅助模块: `backend/causal_mechanism_inferrer.py` (自动推断因果机制)
- 数据结构: `CausalEdge`, `SchemaNode`, `CausalMechanism`

### 因果图谱构建流程

```
warehouse.db (SQLite)
       |
       v
CausalGraphBuilder.build_from_warehouse()
       |
       +---> 扫描所有表的外键约束 -> FOREIGN_KEY edges
       +---> 分析ETL脚本中的列引用 -> ETL_TRANSFORM edges
       +---> 检测视图/报表的列依赖 -> SCHEMA_DEPENDENCY edges
       +---> 计算质量指标相关性 -> QUALITY_PROPAGATION edges
       +---> 分析历史故障传播路径 -> CASCADING_FAILURE edges
       |
       v
CausalLineageGraph (有向加权图)
```

### 量化指标

| 指标 | 公式 | 含义 |
|------|------|------|
| 血缘密度(Lineage Density) | LD = \|E_causal\| / \|V\|^2 | 因果网络稠密程度 |
| 传播效率(Propagation Efficiency) | PE = Σ(1/delay) / \|affected\| | 因果效应传播速度 |
| 脆弱性评分(Fragility Score) | FS = Σ(ImpactProb × Criticality) | 关键节点脆弱程度 |

---

## C2: Do-Calculus 变更影响预测

### 问题背景

传统影响分析基于"哪些表引用了该列"的静态检查，无法回答"如果我执行这个变更操作，下游会受到什么影响"。

### 技术方案

引入Judea Pearl的**Do-Calculus**框架，将Schema变更建模为**干预操作(do-operation)**，计算因果效应P(Y|do(X))。

#### 干预操作类型

```python
DoOperation(op_type="drop_column",     target="orders.email")      # 删除列
DoOperation(op_type="rename_column",   target="sales.amount",      # 重命名
                                     new_name="sales.revenue")
DoOperation(op_type="dtype_change",    target="orders.created_at", # 类型变更
                                     new_dtype="TIMESTAMP")
```

#### 影响预测算法

```
predict_impact(do_operation):
    1. 在因果图谱中定位目标节点
    2. 执行图遍历(BFS/DFS)，沿因果边传播
    3. 对每条路径计算:
       - failure_probability = Π(edge.strength) along path
       - impact_score = edge.confidence × downstream_criticality
    4. 按severity排序返回影响列表
```

### 返回结构

```json
{
    "operation": "do(drop_column(orders.customer_email))",
    "total_affected": 5,
    "effects": [
        {
            "node": "order_analytics",
            "probability": 0.95,
            "severity": "HIGH",
            "explanation": "orders.customer_email -> order_analytics.customer_info via FOREIGN_KEY",
            "etl_jobs": ["ETL_Customer360", "ETL_DailyReport"]
        }
    ]
}
```

---

## C3: 反事实质量推理 (Counterfactual Reasoning)

### 问题背景

数据团队常面临"如果当初没有删除这个字段，数据质量会怎样"的假设性问题。传统工具无法回答此类反事实问题。

### 技术方案

实现基于因果图谱的**反事实推理引擎(CounterfactualReasoner)**，支持三阶段推理：

```
factual:         观察到的实际结果 (ETL_Job_1 质量分=94.5)
counterfactual:  假设变更后的预测结果 (质量分=71.2)
difference:      两者差异 (质量下降=-23.3)
```

#### 推理流程

```
counterfactual(change, outcome):
    1. 记录factual: 当前outcome的实际指标
    2. 在因果图谱中模拟change:
       - 标记目标节点为"已干预"
       - 沿因果边传播干预效应
    3. 计算counterfactual: 预测干预后outcome的指标
    4. 计算difference: factual与counterfactual的差异
    5. 生成mitigation_suggestions: 缓解建议
```

### 应用场景

- Schema变更前的影响评估："如果删除orders.email，ETL_Customer360的质量分会下降多少？"
- 历史回溯分析："如果上个月没有重命名sales.amount，本月的报表异常会不会发生？"
- 变更方案对比："方案A删列 vs 方案B重命名，哪个对下游影响更小？"

---

## C4: 多Agent因果协作 (Multi-Agent Causal Collaboration)

### 问题背景

数据治理涉及多个环节(变更检测、ETL执行、质量监控、报告生成)，传统方案各环节独立运行，缺乏因果感知的协作机制。

### 技术方案

设计四Agent协作架构，每个Agent具备因果推理能力：

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│ Schema Agent│────▶│  ETL Agent  │────▶│Observer Agent│───▶│  Viz Agent  │
│ 变更检测     │     │ 数据清洗     │     │ 质量监控     │     │ 报告告警     │
│ 因果图构建   │     │ 影响预测     │     │ 根因分析     │     │ 可视化       │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
```

#### Agent职责

| Agent | 输入 | 输出 | 因果能力 |
|-------|------|------|----------|
| Schema Agent | warehouse.db | 变更列表 + 因果图 | 构建CausalLineageGraph |
| ETL Agent | 变更列表 | 清洗后数据 + 质量分 | predict_impact() |
| Observer Agent | 质量指标 | 根因分析报告 | counterfactual() |
| Viz Agent | 分析结果 | HTML报告 + 告警 | 可视化因果链路 |

#### 消息协议

```python
@dataclass
class AgentMessage:
    sender: str            # "SchemaAgent"
    receiver: str          # "ETLAgent"
    msg_type: str          # "schema_change_detected"
    payload: dict          # 变更详情
    causal_context: dict   # 因果推理上下文(新增)
```

---

## 代码规模统计

| 模块 | 文件 | 行数 | 核心类 |
|------|------|------|--------|
| 因果推断引擎 | causal_engine.py | 1121 | CausalSchemaEngine, CounterfactualReasoner |
| FastAPI服务 | app.py | 1384 | FastAPI app + 中间件 + 30+端点 |
| Agent Swarm | auto_data_flow.py | 754 | SchemaAgent, ETLAgent, ObserverAgent |
| Schema检测 | schema_change_detector.py | 351 | SchemaSnapshot, SchemaChange |
| 规则引擎 | rule_engine.py | ~400 | DataQualityRuleEngine |
| 合规引擎 | compliance_engine.py | ~300 | ComplianceEngine |
| 报告导出 | report_export.py | ~300 | ReportExporter |
| **合计** | **16个.py文件** | **~5000+** | - |

---

## 专利与论文方向

### 专利方向

1. **因果血缘图谱构建方法** -- 基于5种因果机制的自动化图谱构建
2. **Do-Calculus驱动的Schema变更影响预测** -- 因果推断在数据治理中的应用
3. **反事实数据质量推理系统** -- 基于因果图的假设分析方法
4. **多Agent因果协作数据治理框架** -- 因果感知的Agent间协作协议

### SCI论文方向

- 领域: Data Engineering / Data Quality / Causal Inference
- 创新点: 首次将Pearl因果推断框架应用于Schema演变预测
- 实验: 与Great Expectations / Apache Griffin的对比评估
- 框架文档: `SCI_FRAMEWORK.md` (38KB)

---

## 技术栈

- **因果引擎**: 自研CausalSchemaEngine (Python dataclass)
- **后端框架**: FastAPI + uvicorn + loguru + prometheus-client
- **数据处理**: Polars (高性能DataFrame)
- **规则引擎**: YAML声明式规则 + Polars执行
- **前端**: Vue3 + ECharts 5.4
- **存储**: SQLite (WAL模式) + JSON
- **部署**: Gunicorn多worker + Docker
