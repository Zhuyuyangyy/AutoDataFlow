

"""
AutoDataFlow v2.1 - 数据质量报告生成器
生成 Markdown 格式的数据质量报告，支持定时任务输出
"""
import json
import os
from datetime import datetime

REPORT_TEMPLATE = """# 数据质量健康度报告

**生成时间**: {generated_at}
**数据表数量**: {total_tables}
**总记录数**: {total_rows:,}
**综合质量评分**: {avg_quality_score}/100

---

## 一、各表质量详情

{table_details}

---

## 二、质量趋势分析

{trend_analysis}

---

## 三、AI 预测分析

{predictive_analysis}

---

## 四、数据血缘链路

{lineage_summary}

---

## 五、Schema 变更记录

{schema_changes}

---

## 六、Agent Swarm 状态

| Agent | 状态 | 活跃度 |
|-------|------|--------|
{agent_status}

---

> 本报告由 AutoDataFlow v2.1 自动生成
> 数据健康度治理 — Agent Swarm Monitoring
"""

def generate_quality_report(data_dir: str) -> str:
    """生成完整的数据质量报告"""
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # Load data
    health_path = os.path.join(data_dir, 'health_report.json')
    trend_path = os.path.join(data_dir, 'quality_trend.json')
    pred_path = os.path.join(data_dir, 'predictive_analytics.json')
    lineage_path = os.path.join(data_dir, 'lineage.json')
    schema_path = os.path.join(data_dir, 'schema_changes.json')

    health = _load_json(health_path)
    trend = _load_json(trend_path)
    pred = _load_json(pred_path)
    lineage = _load_json(lineage_path)
    schema = _load_json(schema_path)

    # Build table details
    table_details = ""
    for t in health.get('tables', []):
        qs = t.get('quality_score', 0)
        badge = '🟢' if qs >= 90 else '🟡' if qs >= 70 else '🔴'
        table_details += f"### {badge} {t['name']}\n"
        table_details += f"- 行数: `{t['row_count']:,}` | 质量: **{qs}/100**\n"
        table_details += f"- 空值率: {sum(c.get('null_pct',0) for c in t.get('columns',[]))/max(len(t.get('columns',[])),1):.2%}\n"
        table_details += f"- 异常值: {sum(c.get('outlier_count',0) for c in t.get('columns',[]))} 个\n\n"

    # Trend analysis
    runs = trend.get('history', trend.get('runs', []))
    if runs:
        latest = runs[-1].get('avg_quality_score', runs[-1].get('quality_score', 0))
        prev = runs[-2].get('avg_quality_score', runs[-2].get('quality_score', latest)) if len(runs) > 1 else latest
        delta = latest - prev
        arrow = '↑' if delta > 0 else '↓' if delta < 0 else '→'
        trend_analysis = f"最近1次质量: **{latest}** {arrow} ({delta:+.1f})\n"
        trend_analysis += f"近7期平均: **{sum(r.get('avg_quality_score', r.get('quality_score', 0)) for r in runs[-7:])/min(len(runs[-7:]),7):.1f}**\n"
    else:
        trend_analysis = "暂无趋势数据\n"

    # Predictive
    if pred and pred.get('predictions'):
        p = pred['predictions'][0]
        predictive_analysis = f"- 预测质量: **{p.get('predicted_quality_score', 'N/A')}**\n"
        predictive_analysis += f"- 置信度: **{p.get('confidence', 0)*100:.0f}%**\n"
        if p.get('root_cause'):
            predictive_analysis += f"- 根因: {p['root_cause']}\n"
        if p.get('anomaly_alerts'):
            predictive_analysis += f"- 异常预警: {len(p['anomaly_alerts'])} 项\n"
            for a in p['anomaly_alerts'][:3]:
                predictive_analysis += f"  - **{a.get('field','N/A')}**: {a.get('description','N/A')}\n"
    else:
        predictive_analysis = "暂无预测数据\n"

    # Lineage - handle both list and dict formats
    if isinstance(lineage, list):
        # Column-level lineage: each entry = {table, column, source_file}
        if lineage:
            lineage_summary = f"共 {len(lineage)} 个字段可追溯\n"
            tables_with_lineage = set(e.get('table','') for e in lineage)
            for tbl in sorted(tables_with_lineage)[:5]:
                cols = [e.get('column','') for e in lineage if e.get('table','') == tbl]
                lineage_summary += f"- 📋 {tbl}: {len(cols)} 个字段已建模\n"
        else:
            lineage_summary = "暂无血缘数据\n"
    elif lineage and lineage.get('lineage'):
        lineage_summary = ""
        for node in lineage['lineage']:
            t = node.get('type','unknown')
            icon = '📥' if t == 'source' else '⚙️' if t == 'process' else '📤'
            lineage_summary += f"{icon} **{node.get('name','N/A')}**\n"
    else:
        lineage_summary = "暂无血缘数据\n"

    # Schema changes
    if schema and schema.get('changes'):
        schema_changes = ""
        for c in schema['changes'][:5]:
            tc = c.get('change_type','').upper()
            emoji = '🟢' if tc == 'ADDED' else '🟡' if tc == 'MODIFIED' else '🔴'
            schema_changes += f"{emoji} [{c.get('timestamp','N/A')}] **{tc}**: {c.get('description','N/A')}\n"
            if c.get('impact'):
                schema_changes += f"   → 影响: {c['impact']}\n"
    else:
        schema_changes = "暂无变更记录\n"

    # Agent status (placeholder)
    agent_status = "| DataQualityAgent | 运行中 | ████████░░ 80% |\n"
    agent_status += "| SchemaAgent | 运行中 | ██████████ 100% |\n"
    agent_status += "| LineageAgent | 运行中 | ███████░░░ 70% |\n"

    return REPORT_TEMPLATE.format(
        generated_at=ts,
        total_tables=health.get('total_tables', 0),
        total_rows=health.get('total_rows', 0),
        avg_quality_score=health.get('avg_quality_score', 0),
        table_details=table_details,
        trend_analysis=trend_analysis,
        predictive_analysis=predictive_analysis,
        lineage_summary=lineage_summary,
        schema_changes=schema_changes,
        agent_status=agent_status,
    )


def _load_json(path):
    if os.path.exists(path):
        try:
            with open(path, encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}


def save_report(data_dir: str, output_path: str = None):
    """生成并保存报告"""
    report = generate_quality_report(data_dir)
    if output_path is None:
        data_dir_name = os.path.basename(os.path.dirname(data_dir.rstrip('\\/')))
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_path = os.path.join(data_dir, f'quality_report_{ts}.md')
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(report)
    return output_path


if __name__ == '__main__':
    data_dir = r'D:\GITHUB\openclaw-2026.4.10\workplace\projects\AutoDataFlow\backend\data'
    output = save_report(data_dir)
    print(f'Report saved to: {output}')