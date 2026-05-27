# AutoDataFlow v3.0 — 因果驱动的Schema演变预测平台

> **版本**: v3.0 | **技术栈**: FastAPI + Vue3 + ECharts + 因果推断引擎
> **状态**: 🚀 生产就绪 | **核心**: CausalLineageGraph + Do-Calculus + CounterfactualReasoner

---

## 🎯 项目定位

AutoDataFlow 从"Schema变更检测工具"升级为**因果驱动的Schema演变预测框架**。

**核心价值**: 在Schema变更发生之前，预测其对下游ETL任务的影响范围，实现从被动响应到主动预防的跃迁。

---

## 🔬 四大核心创新点

| 创新点 | 代码模块 | 技术方案 | 效果 |
|--------|----------|----------|------|
| **C1 因果血缘图谱** | `causal_engine.py` | CausalEdge(5种机制) + SchemaNode | 从"相关性"升级到"因果性" |
| **C2 Do-Calculus变更预测** | `CausalSchemaEngine.predict_impact()` | P(Y\|do(X)) 干预推理 | 提前识别受影响的ETL任务 |
| **C3 反事实质量推理** | `CounterfactualReasoner` | factual/counterfactual/difference | 回答"如果...会怎样" |
| **C4 多Agent因果协作** | `app.py` | Schema→ETL→Observer→Viz Agent | 端到端自治 |

---

## 🏗️ 系统架构

### v3.0 完整架构

```
┌─────────────────────────────────────────────────────────────────┐
│                    AutoDataFlow v3.0 完整架构                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────┐    do(change)    ┌──────────────────────┐     │
│  │ Schema Agent│ ──────────────▶ │  因果血缘图谱构建器   │     │
│  └─────────────┘                  │ CausalLineageGraph   │     │
│         │                         └──────────┬───────────┘     │
│         │                                    │                 │
│         ▼                           ┌────────▼───────────┐     │
│  ┌─────────────┐                    │  Do-Calculus 推理  │     │
│  │ ETL Agent   │ ◀─────────────────│ CausalSchemaEngine │     │
│  └─────────────┘   predict_impact()└──────────┬─────────┘     │
│         │                                    │                 │
│         ▼                           ┌────────▼───────────┐     │
│  ┌─────────────┐                    │ 反事实推理引擎    │     │
│  │Observer Agent│ ◀────────────────│CounterfactualReas.│     │
│  └─────────────┘  what-if分析       └──────────┬─────────┘     │
│         │                                    │                 │
│         ▼                           ┌────────▼───────────┐     │
│  ┌─────────────┐                    │  Viz Agent         │     │
│  │  WebSocket  │ ◀─────────────────│  报告+告警+可视化  │     │
│  └─────────────┘                    └───────────────────┘     │
└─────────────────────────────────────────────────────────────────┘
```

### 旧版(v2.2)与v3.0对比

| 能力 | v2.2 看板 | v3.0 因果推断 |
|------|-----------|--------------|
| Schema变更检测 | ✅ 被动检测变更 | ✅ 主动预测影响 |
| ETL影响分析 | ❌ 无 | ✅ P(Y\|do(X))量化 |
| 因果关系 | ❌ 数据血缘(相关性) | ✅ CausalEdge(因果性) |
| 反事实推理 | ❌ 无 | ✅ "如果删除X..." |
| 多Agent协作 | ❌ 单系统 | ✅ 四Agent闭环 |

---

## 📁 项目结构

```
AutoDataFlow/
├── backend/
│   ├── app.py                    # FastAPI主应用 + 四Agent集成
│   ├── auto_data_flow.py         # v2.2数据刷新模块(兼容)
│   ├── schema_change_detector.py # Schema变更检测器
│   ├── causal_engine.py          # 🚀 v3.0核心: 因果推断引擎
│   ├── data/                     # 数据文件(JSON)
│   │   ├── health_report.json
│   │   ├── quality_trend.json
│   │   ├── predictive_analytics.json
│   │   ├── schema_changes.json
│   │   ├── lineage.json
│   │   └── data_sources.json
│   └── webhook_alert.py         # 飞书/钉钉告警
├── frontend/
│   └── index.html                # Vue3 + ECharts 可视化看板
└── docs/
    ├── SCI_FRAMEWORK.md          # SCI论文框架(38KB)
    ├── 专利技术交底书.md           # 专利文档(48KB)
    └── README.md                 # 本文件
```

---

## 🚀 快速启动

### 启动 v3.0 因果推断引擎

```bash
cd /mnt/d/ZYY Project/AutoDataFlow/backend

# 方式1: 独立使用因果引擎
python -c "
from causal_engine import CausalSchemaEngine

engine = CausalSchemaEngine()
engine.build_from_warehouse('data/warehouse.db')

# 预测影响
result = engine.predict_impact(
    do_operation='drop_column',
    target='orders.customer_email'
)
print(result)

# 反事实推理
cf = engine.counterfactual(
    change={'type': 'rename_column', 'target': 'sales.amount', 'new_name': 'revenue'},
    outcome='ETL_Job_1'
)
print(cf)
"

# 方式2: 启动完整API服务
python app.py  # 端口8080
```

### API端点(v3.0)

```
GET  /health                    # 系统健康检查
POST /causal/graph/build        # 构建因果血缘图谱
POST /causal/impact/predict    # Do-Calculus变更影响预测
POST /causal/counterfactual     # 反事实推理
GET  /causal/metrics            # SchemaCausalityMetrics查询
WS   /ws/stream                 # WebSocket实时流
```

---

## 🧠 核心模块详解

### 1. causal_engine.py — 因果推断引擎

```python
from causal_engine import (
    CausalSchemaEngine,
    CausalMechanism,
    CausalEdge,
    SchemaNode,
    DoOperation,
    CounterfactualReasoner
)

# 初始化引擎
engine = CausalSchemaEngine()

# 构建因果图谱
engine.build_causal_graph(sources=['MySQL', 'PostgreSQL'])

# ─────────────────────────────────────────────────
# C1: 因果边示例 — 5种因果机制
# ─────────────────────────────────────────────────

# 机制1: FOREIGN_KEY — 外键因果
edge1 = CausalEdge(
    source_table='orders',
    source_column='customer_id',
    target_table='customers',
    target_column='id',
    mechanism=CausalMechanism.FOREIGN_KEY,
    strength=1.0,
    confidence=0.95
)

# 机制2: ETL_TRANSFORM — ETL变换因果
edge2 = CausalEdge(
    source_table='raw_sales',
    source_column='amount',
    target_table='daily_summary',
    target_column='total_revenue',
    mechanism=CausalMechanism.ETL_TRANSFORM,
    strength=0.85,
    confidence=0.90
)

# 机制3: SCHEMA_DEPENDENCY — Schema依赖因果
edge3 = CausalEdge(
    source_table='orders',
    source_column='status',
    target_table='etl_job_order_report',
    target_column='status',
    mechanism=CausalMechanism.SCHEMA_DEPENDENCY,
    strength=0.70,
    confidence=0.80
)

# 机制4: QUALITY_PROPAGATION — 质量传播因果
edge4 = CausalEdge(
    source_table='products',
    source_column='stock',
    target_table='order_fulfillment',
    target_column='delivery_time',
    mechanism=CausalMechanism.QUALITY_PROPAGATION,
    strength=0.60,
    confidence=0.75
)

# 机制5: CASCADING_FAILURE — 级联故障因果
edge5 = CausalEdge(
    source_table='payments',
    source_column='status',
    target_table='order_fulfillment',
    target_column='shipped_at',
    mechanism=CausalMechanism.CASCADING_FAILURE,
    strength=0.50,
    confidence=0.85
)
```

### 2. Do-Calculus 变更影响预测

```python
# ─────────────────────────────────────────────────
# C2: Do-Calculus 干预操作
# ─────────────────────────────────────────────────

# 操作1: 删除列
do_drop = DoOperation(
    op_type='drop_column',
    target='orders.customer_email',
    new_value=None
)

# 操作2: 重命名列
do_rename = DoOperation(
    op_type='rename_column',
    target='sales.amount',
    new_name='sales.revenue'
)

# 操作3: 数据类型变更
do_dtype = DoOperation(
    op_type='dtype_change',
    target='orders.created_at',
    new_dtype='TIMESTAMP'
)

# 预测影响 — 核心方法
result = engine.predict_impact(do_operation=do_drop)

# 返回结构
{
    'operation': 'drop_column(orders.customer_email)',
    'downstream_tables': ['order_analytics', 'customer360', 'etl_daily_report'],
    'affected_etl_jobs': [
        {'job': 'ETL_Customer360', 'failure_prob': 0.95, 'impact_score': 0.85},
        {'job': 'ETL_DailyReport', 'failure_prob': 0.60, 'impact_score': 0.50}
    ],
    'severity': 'HIGH',
    'recommendations': [
        '建议在删除前先添加nullable版本(v2)并双写',
        'ETL_Customer360需要优先修改',
        '预计影响2000万条历史数据'
    ]
}
```

### 3. CounterfactualReasoner — 反事实推理

```python
# ─────────────────────────────────────────────────
# C3: 反事实推理 — "如果...会怎样?"
# ─────────────────────────────────────────────────

# factual: 实际观察到的结果
factual = engine.query_factual(outcome='ETL_Job_1', time_range='7d')

# counterfactual: 反事实假设
counterfactual = engine.counterfactual(
    change={
        'type': 'drop_column',
        'target': 'orders.customer_email'
    },
    outcome='ETL_Job_1',
    time_range='7d'
)

# difference: factual vs counterfactual 的差异
difference = engine.compute_difference(factual, counterfactual)

# 返回结构
{
    'factual': {
        'outcome': 'ETL_Job_1',
        'actual_quality_score': 94.5,
        'anomaly_count': 2
    },
    'counterfactual': {
        '假设操作': 'drop_column(orders.customer_email)',
        'predicted_quality_score': 71.2,
        'anomaly_count': 15,
        'affected_columns': ['customer_email', 'customer_name_from_email']
    },
    'difference': {
        'quality_degradation': -23.3,
        'anomaly_increase': 13,
        'critical_operations': ['customer360_report', 'email_notification_job']
    }
}
```

### 4. SchemaCausalityMetrics — 因果指标

```python
# ─────────────────────────────────────────────────
# C4: Schema因果指标 — 量化血缘质量
# ─────────────────────────────────────────────────

metrics = engine.compute_schema_metrics()

# 指标1: 血缘密度 (Lineage Density)
# 公式: LD = |E_causal| / |V|²
# 意义: 每个节点平均连接数，衡量血缘网络稠密程度
{
    'lineage_density': 0.073,  # 7.3%连通率
    'total_nodes': 156,
    'total_edges': 1789,
    'avg_degree': 22.9
}

# 指标2: 传播效率 (Propagation Efficiency)
# 公式: PE = Σ(1/delay_steps) / |affected_nodes|
# 意义: 因果效应从源头到终端的速度
{
    'propagation_efficiency': 0.68,
    'avg_delay_steps': 2.3,
    'fast_propagation_paths': 342,
    'bottleneck_nodes': ['etl_core_aggregation']
}

# 指标3: 脆弱性评分 (Fragility Score)
# 公式: FS = Σ(ImpactProb_i × JobCriticality_i)
# 意义: 识别最容易被变更影响的关键ETL任务
{
    'fragility_score': 0.52,
    'critical_jobs': [
        {'job': 'ETL_Customer360', 'fragility': 0.89},
        {'job': 'ETL_FinanceReport', 'fragility': 0.82},
        {'job': 'ETL_InventorySync', 'fragility': 0.75}
    ],
    'bottleneck_columns': ['orders.customer_email', 'payments.amount']
}
```

---

## 📊 健康度计算公式

### 综合健康度 (Causally Weighted)

$$
HealthScore = 100 \times \frac{1 - \sum_{i}{\alpha_i \cdot IssueRate_i}}{\sum_{i}{\alpha_i}}
$$

其中:
- $IssueRate_i = null\_pct_i + outlier\_pct_i$
- $\alpha_i = PageRank(causal\_graph, column_i) \times field\_importance_i$
- $PageRank$: 在因果图中的重要性（影响越多下游表，权重越高）

**关键区别**: 普通健康度对所有字段平等加权；因果健康度对"影响下游ETL的关键字段"给予更高权重。

### Schema变更影响严重度

$$
Severity(change) = \sum_{job \in downstream} ImpactLocalization(job, change) \times JobCriticality(job)
$$

其中:
- $ImpactLocalization = 1$ 如果变更目标 ∈ job的读取列，否则 $0$
- $JobCriticality = upstream\_quality\_score \times downstream\_dependency\_count$

---

## 🧪 四Agent协作流程

### Agent间消息格式

```python
@dataclass
class AgentMessage:
    sender: str              # "SchemaAgent"
    receiver: str            # "ETLAgent"
    msg_type: str            # "schema_change_detected" | "etl_completed" | "anomaly_found"
    payload: dict            # 消息内容
    timestamp: str           # ISO格式
    causal_context: dict     # 因果推理上下文
```

### 协作时序

```
Schema Agent                              ETL Agent
     │                                        │
     │ 1. 检测到Schema变更                    │
     │   {change: drop_column(orders.email)}  │
     │ ────────────────────────────────────▶  │
     │                                        │
     │                        2. 因果分析      │
     │                        预测影响范围     │
     │                                        │
     │ ◀───────────────────────────────────  │
     │   {affected_jobs: [ETL_C360, ETL_REP]} │
     │                                        │
     │                                        ▼
     │                                   ETL Agent
     │                                        │
     │ 3. 执行ETL并检测异常                   │
     │   {anomaly: quality_dropped}           │
     │ ────────────────────────────────────▶  │
     │                                        ▼
     │                               Observer Agent
     │                                        │
     │ 4. 根因分析                            │
     │   {root_cause: quality_propagation}    │
     │ ────────────────────────────────────▶  │
     │                                        ▼
     │                                   Viz Agent
     │                                        │
     │ 5. 生成报告+告警                       │
     │   {report_url, alert_channels}         │
```

---

## 🔧 配置说明

### 因果引擎配置

```yaml
# backend/config/causal_config.yaml
causal_engine:
  # 因果推断参数
  confidence_threshold: 0.75   # 最小置信度
  propagation_depth: 10       # 最大传播深度
  
  # Do-Calculus参数
  do_calculus:
    max_downstream_depth: 5   # 最大下游深度
    failure_prob_threshold: 0.5
  
  # 反事实推理参数
  counterfactual:
    num_samples: 1000
    time_horizon: '7d'
  
  # 因果图谱存储
  storage:
    backend: 'sqlite'
    path: 'data/warehouse.db'

  # Agent配置
  agents:
    schema:
      scan_interval: 300  # 5分钟扫描一次
    etl:
      retry_count: 3
      timeout: 60
```

---

## 📈 与现有工具对比

| 能力 | Great Expectations | Apache Griffin | AutoDataFlow v3.0 |
|------|-------------------|-----------------|-------------------|
| Schema变更检测 | ❌ | ❌ | ✅ |
| 因果血缘图谱 | ❌ | ❌ | ✅ |
| Do-Calculus预测 | ❌ | ❌ | ✅ |
| 反事实推理 | ❌ | ❌ | ✅ |
| 多Agent协作 | ❌ | ❌ | ✅ |
| 变更影响量化 | ⚠️ 规则匹配 | ⚠️ 统计检测 | ✅ 因果推理 |
| 预测 vs 检测 | 检测 | 检测 | **预测** |

---

## 🎯 适用场景

1. **数据库Schema升级前**: 预测字段变更对下游ETL的影响范围
2. **微服务重构**: 评估数据库拆分对上游应用的影响
3. **数据湖治理**: 识别关键血缘节点，制定保护策略
4. **ETL作业调度**: 变更Schema后自动重排依赖任务

---

## 📝 技术栈

- **后端**: Python 3.10+ / FastAPI / asyncio
- **因果引擎**: 自研CausalSchemaEngine (1121行)
- **数据存储**: SQLite (图谱) + JSON (看板数据)
- **前端**: Vue3 + ECharts 5.4
- **告警**: 飞书 Webhook + 钉钉 Webhook
- **协议**: REST API + WebSocket

---

## 🔗 相关文档

- 📄 [SCI_FRAMEWORK.md](SCI_FRAMEWORK.md) — SCI论文框架(38KB)
- 📄 [专利技术交底书.md](专利技术交底书.md) — 专利文档(48KB)
- 💻 [backend/app.py](backend/app.py) — FastAPI主应用
- 💻 [backend/causal_engine.py](backend/causal_engine.py) — 因果推断引擎(43KB)