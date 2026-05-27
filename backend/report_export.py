#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AutoDataFlow 报告导出引擎（PDF / HTML）
======================================
生成格式化的数据质量报告：

1. HTML 报告 — 完整可视化报告（表格、图表、评分卡片）
2. PDF 导出 — 通过 weasyprint 将 HTML 转为 PDF

使用方式：
    exporter = ReportExporter("data/warehouse.db", "backend/config")
    html_path = exporter.generate_html_report()
    pdf_path = exporter.generate_pdf_report()
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import polars as pl

from rule_engine import DataQualityRuleEngine
from compliance_engine import ComplianceEngine


# ============================================================
# 1. HTML 报告生成器
# ============================================================

class HTMLReportGenerator:
    """生成带样式美化数据的 HTML 报告"""

    @staticmethod
    def _grade_badge(score: float) -> str:
        if score >= 90:
            return "🟢 优秀"
        elif score >= 75:
            return "🟡 良好"
        elif score >= 60:
            return "🟠 一般"
        return "🔴 差"

    @staticmethod
    def _severity_color(severity: str) -> str:
        return {
            "critical": "#dc3545",
            "warning": "#fd7e14",
            "info": "#0dcaf0",
        }.get(severity, "#6c757d")

    def generate(self, db_path: str, config_dir: Path, output_path: Path) -> Path:
        """生成完整的 HTML 质量报告"""
        rule_engine = DataQualityRuleEngine(db_path, config_dir)
        compliance_engine = ComplianceEngine(db_path, config_dir)
        rule_result = rule_engine.validate_all()
        compliance_summary = compliance_engine.get_compliance_summary()

        # Load health data
        data_dir = Path(db_path).parent
        health_data = {}
        trend_data = {}
        pred_data = {}
        for fname in ("health_report.json", "quality_trend.json", "predictive_analytics.json"):
            p = data_dir / fname
            if p.exists():
                try:
                    if fname == "health_report.json":
                        health_data = json.loads(p.read_text(encoding="utf-8"))
                    elif fname == "quality_trend.json":
                        trend_data = json.loads(p.read_text(encoding="utf-8"))
                    elif fname == "predictive_analytics.json":
                        pred_data = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    pass

        # Build table rows HTML
        tables_html = ""
        for tr in rule_result.table_results:
            grade = self._grade_badge(tr.quality_score)
            violations_html = ""
            for v in tr.violations[:5]:
                color = self._severity_color(v.severity)
                violations_html += f"""
                <tr style="color:{color}">
                    <td>{v.rule_id}</td>
                    <td>{v.field}</td>
                    <td><span style="background:{color}22;color:{color};padding:2px 6px;border-radius:4px">{v.severity}</span></td>
                    <td>{v.message}</td>
                    <td>{v.failed_count}</td>
                </tr>"""
            tables_html += f"""
            <tr>
                <td><strong>{tr.table}</strong></td>
                <td>{tr.total_rules}</td>
                <td>{tr.passed}</td>
                <td>{tr.failed}</td>
                <td style="color:{'green' if tr.quality_score>=80 else 'orange' if tr.quality_score>=60 else 'red'}"><strong>{tr.quality_score:.1f}</strong></td>
                <td>{grade}</td>
                <td><button class="toggle-btn" onclick="toggleViolations('{tr.table}')">查看</button></td>
            </tr>
            <tr id="violations-{tr.table}" class="violations-row" style="display:none">
                <td colspan="7">
                    <table class="violations-table">
                        <thead><tr><th>规则ID</th><th>字段</th><th>严重性</th><th>说明</th><th>违规数</th></tr></thead>
                        <tbody>{violations_html}</tbody>
                    </table>
                </td>
            </tr>"""

        # Trend data
        trend_rows = ""
        history = trend_data.get("history", [])
        for h in history[-10:]:
            trend_rows += f"""<tr><td>{h.get('timestamp','')}</td>
                <td>{h.get('avg_quality_score', h.get('quality_score', 0)):.1f}</td>
                <td>{h.get('tables','-')}</td></tr>"""

        # Compliance violations
        compliance_html = ""
        by_standard = compliance_summary.get("by_standard", {})
        for std, report in by_standard.items():
            for v in report.get("violations", [])[:5]:
                color = self._severity_color(v.get("severity", "warning"))
                compliance_html += f"""<tr style="color:{color}">
                    <td>{std.upper()}</td>
                    <td>{v.get('table','')}</td>
                    <td>{v.get('field','')}</td>
                    <td>{v.get('description','')}</td>
                    <td><span style="background:{color}22;color:{color};padding:2px 6px;border-radius:4px">{v.get('severity','')}</span></td>
                </tr>"""

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>数据质量健康度报告</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        background: #f5f7fa; color: #333; padding: 24px; }}
.header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
          color: white; padding: 32px; border-radius: 16px; margin-bottom: 24px; }}
.header h1 {{ font-size: 28px; margin-bottom: 8px; }}
.header p {{ opacity: 0.9; font-size: 14px; }}
.card {{ background: white; border-radius: 12px; padding: 20px; margin-bottom: 20px;
         box-shadow: 0 2px 8px rgba(0,0,0,0.06); }}
.card h2 {{ font-size: 16px; color: #555; margin-bottom: 16px; border-bottom: 2px solid #667eea;
           padding-bottom: 8px; }}
.score-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 16px; }}
.score-card {{ background: linear-gradient(135deg, #f8f9fa, #e9ecef); border-radius: 10px;
              padding: 20px; text-align: center; }}
.score-card .value {{ font-size: 32px; font-weight: bold; color: #667eea; }}
.score-card .label {{ font-size: 12px; color: #888; margin-top: 4px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
th {{ background: #f8f9fa; padding: 10px 12px; text-align: left; font-size: 12px;
      color: #666; text-transform: uppercase; letter-spacing: 0.5px; }}
td {{ padding: 10px 12px; border-bottom: 1px solid #f0f0f0; }}
tr:hover {{ background: #f8f9fa; }}
.toggle-btn {{ background: #667eea; color: white; border: none; padding: 4px 12px;
              border-radius: 6px; cursor: pointer; font-size: 12px; }}
.toggle-btn:hover {{ background: #764ba2; }}
.violations-table {{ margin-top: 8px; background: #fafafa; }}
.violations-table th {{ background: #eee; }}
.badge-critical {{ background: #dc354520; color: #dc3545; padding: 2px 8px; border-radius: 4px; font-size: 12px; }}
.badge-warning {{ background: #fd7e1420; color: #fd7e14; padding: 2px 8px; border-radius: 4px; font-size: 12px; }}
.alert-box {{ background: #fff3cd; border-left: 4px solid #fd7e14; padding: 12px 16px;
             border-radius: 4px; margin-bottom: 12px; font-size: 14px; }}
.footer {{ text-align: center; color: #aaa; font-size: 12px; margin-top: 24px; }}
</style>
</head>
<body>
<div class="header">
  <h1>📊 数据质量健康度报告</h1>
  <p>生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} &nbsp;|&nbsp;
     AutoDataFlow v3.0 &nbsp;|&nbsp; 因果推理驱动数据治理</p>
</div>

<div class="card">
  <h2>📈 综合质量评分</h2>
  <div class="score-grid">
    <div class="score-card">
      <div class="value">{rule_result.overall_score:.1f}</div>
      <div class="label">规则质量评分 /100</div>
    </div>
    <div class="score-card">
      <div class="value">{health_data.get('avg_quality_score', rule_result.overall_score):.1f}</div>
      <div class="label">健康度评分 /100</div>
    </div>
    <div class="score-card">
      <div class="value">{compliance_summary.get('overall_compliance_score', 0):.1f}</div>
      <div class="label">合规评分 /100</div>
    </div>
    <div class="score-card">
      <div class="value">{rule_result.total_tables}</div>
      <div class="label">数据表数量</div>
    </div>
    <div class="score-card">
      <div class="value">{health_data.get('total_rows', 0):,}</div>
      <div class="label">总记录数</div>
    </div>
    <div class="score-card">
      <div class="value">{len(rule_result.critical_violations)}</div>
      <div class="label">严重违规数</div>
    </div>
  </div>
</div>

<div class="card">
  <h2>📋 各表规则执行结果</h2>
  <table>
    <thead><tr><th>表名</th><th>规则总数</th><th>通过</th><th>失败</th><th>质量分</th><th>等级</th><th>操作</th></tr></thead>
    <tbody>{tables_html}</tbody>
  </table>
</div>

<div class="card">
  <h2>📈 质量趋势（最近10期）</h2>
  <table>
    <thead><tr><th>时间</th><th>平均质量分</th><th>表数</th></tr></thead>
    <tbody>{trend_rows}</tbody>
  </table>
</div>

<div class="card">
  <h2>⚖️ 合规违规详情</h2>
  {"<div class='alert-box'>✅ 暂无合规违规项，数据符合各项监管标准</div>" if not compliance_html else ""}
  <table>
    <thead><tr><th>标准</th><th>表</th><th>字段</th><th>说明</th><th>严重性</th></tr></thead>
    <tbody>{compliance_html}</tbody>
  </table>
</div>

<div class="footer">
  本报告由 AutoDataFlow v3.0 自动生成 | 数据健康度治理 — 因果推理驱动<br>
  如有疑问请联系数据治理团队
</div>

<script>
function toggleViolations(table) {{
  var row = document.getElementById('violations-' + table);
  if (row.style.display === 'none') {{
    row.style.display = 'table-row';
  }} else {{
    row.style.display = 'none';
  }}
}}
</script>
</body>
</html>"""

        output_path.write_text(html, encoding="utf-8")
        return output_path


# ============================================================
# 2. PDF 报告导出器
# ============================================================

class PDFReportExporter:
    """通过 weasyprint 将 HTML 转为 PDF"""

    @staticmethod
    def is_available() -> bool:
        try:
            import weasyprint
            return True
        except ImportError:
            return False

    @staticmethod
    def generate(html_path: Path, pdf_path: Path) -> Optional[Path]:
        if not PDFReportExporter.is_available():
            return None
        import weasyprint
        try:
            weasyprint.HTML(filename=str(html_path)).write_pdf(str(pdf_path))
            return pdf_path
        except Exception as e:
            print(f"PDF generation failed: {e}")
            return None


# ============================================================
# 3. 统一报告导出器
# ============================================================

class ReportExporter:
    """
    统一报告导出接口：
    - HTML 报告（始终可用）
    - PDF 报告（需要 weasyprint）
    """

    def __init__(self, db_path: str, config_dir: Path):
        self.db_path = db_path
        self.config_dir = config_dir
        self.html_generator = HTMLReportGenerator()

    def _get_output_dir(self) -> Path:
        p = Path(self.db_path).parent
        (p / "reports").mkdir(exist_ok=True)
        return p / "reports"

    def generate_html_report(self) -> Path:
        """生成 HTML 质量报告"""
        output_dir = self._get_output_dir()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = output_dir / f"quality_report_{ts}.html"
        return self.html_generator.generate(self.db_path, self.config_dir, output_path)

    def generate_pdf_report(self) -> Optional[Path]:
        """生成 PDF 质量报告（依赖 weasyprint）"""
        html_path = self.generate_html_report()
        output_dir = self._get_output_dir()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        pdf_path = output_dir / f"quality_report_{ts}.pdf"

        if PDFReportExporter.is_available():
            result = PDFReportExporter.generate(html_path, pdf_path)
            if result is not None:
                return result

        # Fallback: 返回 HTML 路径，提示用户通过浏览器打印为 PDF
        return html_path

    def get_latest_reports(self) -> Dict[str, Any]:
        """获取最新生成的报告列表"""
        output_dir = self._get_output_dir()
        html_reports = sorted(output_dir.glob("quality_report_*.html"), key=lambda p: p.stat().st_mtime, reverse=True)
        pdf_reports = sorted(output_dir.glob("quality_report_*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
        return {
            "html": [{"name": p.name, "path": str(p), "size_kb": round(p.stat().st_size / 1024, 1)}
                     for p in html_reports[:5]],
            "pdf": [{"name": p.name, "path": str(p), "size_kb": round(p.stat().st_size / 1024, 1)}
                    for p in pdf_reports[:5]],
            "weasyprint_available": PDFReportExporter.is_available(),
        }


# ============================================================
# 入口（验证）
# ============================================================

if __name__ == "__main__":
    db = str(Path(__file__).parent / "data" / "warehouse.db")
    cfg = Path(__file__).parent / "config"
    exporter = ReportExporter(db, cfg)

    html_path = exporter.generate_html_report()
    print(f"HTML 报告已生成: {html_path}")

    pdf_path = exporter.generate_pdf_report()
    if pdf_path:
        print(f"PDF 报告已生成: {pdf_path}")
    else:
        print("PDF 需要安装 weasyprint: pip install weasyprint")
        print("  或在浏览器中打开 HTML 报告，使用 Ctrl+P 导出 PDF")